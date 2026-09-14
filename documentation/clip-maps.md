# Clip maps vers alpha cut — v0.17.0

Le convertisseur C compose désormais les clip maps d'image compatibles dans
l'alpha de la texture de couleur, puis exporte **`alphaMode: "MASK"` et
`alphaCutoff: 0.5`**. Les pixels rejetés n'écrivent pas dans le z-buffer ; les
pixels conservés sont opaques. Cela fait partie du
[cœur de glTF 2.0](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#alpha-coverage),
sans extension. OBJ reçoit une image d'opacité binaire via `map_d` ; son importeur
reste libre de filtrer cette image et ne garantit pas un mode alpha test.

## Cas Butterfly

`content/butterfly-tank/butterfly.lwo` contient la couleur des ailes, mais son
clip map se trouve sur les instances des scènes LWS. Deux obstacles empêchaient
le détourage : ces paramètres n'étaient pas évalués, et le masque
`butterfly_clipmap.psd` est un PSD gris mono-canal que stb ne décode pas.

Un lecteur C complémentaire lit maintenant le composite des PSD gris
mono-canal, en 8 ou 16 bits, sans compression ou avec PackBits. Les sections,
dimensions et longueurs des lignes sont vérifiées avant lecture/allocation,
selon la [spécification Adobe](https://www.adobe.com/devnet-apps/photoshop/fileformatashtml/).
Les calques natifs restent archivés dans le PSD ; leur composition n'est pas
recalculée. Les PSD RGB continuent à utiliser stb. Les PSD gris avec canaux
supplémentaires, ZIP, PSB, bitmap 1 bit et flottants ne sont pas ajoutés à ce profil.

Dans les sept scènes Butterfly, `Negative 1` transforme le blanc du masque en
surface conservée. Centre, axe Y et taille de chaque projection correspondent
au matériau de son aile ; la taille négative de l'une des ailes reste respectée.

Le batch corrigé est :

`output/qa-clipmaps-20260914/batch-20260914-095021/packages/butterfly-tank/`

Ouvrir **`gltf/butterfly.lwo.gltf`**, avec son `.bin` et son dossier `textures/`.
Les anciens lots ne sont pas modifiés. La même correction fonctionne dans
les sept glTF de scène, dont `01_butterfly.lws.gltf`.

![Papillon détouré, glTF importé dans Blender](diagnostics/butterfly-clipmap.png)

La QA du batch porte sur :

- 14 clip maps évalués dans 7 scènes ; 16 matériaux détourés, objet isolé compris.
- Alpha des PNG **identique pixel par pixel au masque PSD de 215 × 190** :
  25 454 pixels conservés et 15 396 rejetés au seuil de 0,5.
- RGB inchangé par rapport au précédent export ; buffer de géométrie, normales
  et UV du papillon également inchangé.
- 10 glTF validés par Khronos : aucune erreur ni avertissement. Les huit
  informations restantes signalent des dimensions d'image non puissances de deux.
- Imports OBJ et glTF avec leurs seuls fichiers exportés dans Blender 4.2,
  textures chargées et branchements Alpha vérifiés sur les deux matériaux.
- 13 groupes de tests passent en Release et avec AddressSanitizer, notamment
  les tests de skinning, d'IK, de normales et de projections existants.

Rapports : [pixels et provenance](diagnostics/butterfly-clipmap-qa.json),
[validateur Khronos](diagnostics/butterfly-clipmap-gltf-validation.json),
[import Blender](diagnostics/butterfly-clipmap-blender.json).
Le projet reste `partial` pour ses autres fonctions encore approximées ou
préservées, notamment certains modes de mélange. Ce contrôle ne reproduit pas
l'éclairage original LightWave.

## Portée et séparation des données

Les clip maps sont des attributs **d'instance LWS**, pas des surfaces LWO.
Le matériau natif, ses canaux et ses images ne sont pas remplacés dans l'IR.
`scene.json` conserve l'arbre de paramètres et indique les nombres de matériaux
évalués et ignorés. Les interprétations sont stockées séparément dans
`object.json/derived_clip_maps`, avec les PNG, les empreintes et les plages
d'octets des scènes sources. Les scènes et masques utilisés sont archivés sous
`IR/<objet>/clipmaps/`. Le batch conserve ces fichiers lors de la publication.

Deux instances qui diffèrent par leur clip map obtiennent des variantes de
matériau/mesh dans le glTF. Les instances identiques continuent de partager leur
mesh. Une instance sans clip map conserve son apparence ordinaire. La
transparence de surface existante continue à utiliser `BLEND` lorsqu'aucun
clip map n'est appliqué.

Pour un `.lwo` converti directement, le convertisseur examine les LWS du même
dossier et de ses descendants, puis résout leurs références à cet objet. Il
reprend une apparence détourée par matériau seulement si **toutes les
utilisations trouvées sont évaluables et produisent la même texture alpha**.
Une utilisation sans masque ou un masque différent bloque cette reprise ;
une référence ambiguë ou une scène illisible bloque l'inférence. La recherche
est limitée à 128 scènes et ne remonte pas dans les dossiers parents.
Le nom de l'image seul n'est jamais une preuve de son rôle de clip map.

Lors de la conversion d'une scène, l'aperçu séparé de ses objets emploie le
consensus des instances de cette scène. Comme pour les autres propriétés
d'aperçu, le batch publie l'objet partagé depuis sa première conversion ; les
fichiers de scène gardent leurs propres variantes. Pour examiner le consensus
de toutes les scènes voisines, convertir directement le `.lwo`.

Le seuil 0,5 est un **choix d'export**, pas une valeur native prétendument
retrouvée. La couverture est `1 - valeur_du_clip`, après `Negative`, avec une
valeur scalaire obtenue par moyenne RGB et pondération de l'alpha de l'image.
Elle multiplie l'alpha de couleur/transparence déjà dérivé. Dans ce profil
binaire, une transparence partielle peut donc être seuillée. `ObjectDissolve`
et son animation restent distincts et non évalués.

## Qualification actuelle

| Cas | Évaluation |
| --- | --- |
| `ClipMaps / TextureBlock`, une image active, mode normal à 100 % | Oui |
| Projection plane ou sphérique, axes X/Y/Z, transformation statique | Oui, alignée sur la projection existante du matériau |
| Matériau projeté d'un objet LWOB ou LWO2 | Oui |
| Repeat, Mirror, Reset, Edge | Oui, modes identiques au matériau ; réutilisation de son atlas éventuel |
| Projections différentes entre couleur et masque, matériau sans projection d'image | Préservées, sans composition |
| Projection UV explicite du clip, cubique, cylindrique, frontale | Préservées, sans évaluation par ce profil |
| Référence à un autre objet, coordonnées monde, falloff, enveloppes | Préservés, sans évaluation |
| Plusieurs couches, procédurales, gradients, modes de mélange complexes | Préservés, sans évaluation |
| Ancienne syntaxe `ClipMap` suivie de `Texture...` | Préservée ; évaluateur legacy non qualifié |
| Backend `.blend` natif | Différé ; le glTF exporté est importable dans Blender |

La comparaison des paramètres emploie une tolérance de 10⁻⁶ pour les écarts
d'arrondi entre le texte LWS et les flottants LWO. Aucun remeshing ni subdivision
n'est introduit. Les limites de raster du convertisseur restent applicables.

Code : [qualification et provenance](../src/clipmaps.c),
[composition](../src/textures.c), [PSD gris](../src/raster.c),
[tests de régression](../tests/test_clipmaps.py).

```powershell
python -X utf8 tools/batch_convert.py --project butterfly-tank --output-root output/qa-clipmaps-new
python -X utf8 tests/test_clipmaps.py bin/win64/lwconvert.exe
python -X utf8 documentation/diagnostics/check_butterfly_clipmap.py --project output/qa-clipmaps-20260914/batch-20260914-095021/packages/butterfly-tank --source content/butterfly-tank --baseline output/qa-projections-20260914/batch-20260914-085207/packages/butterfly-tank --report documentation/diagnostics/butterfly-clipmap-qa.json
```
