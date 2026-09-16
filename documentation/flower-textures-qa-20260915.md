# Flower / Prune : QA des textures du 15 septembre 2026

## `chair.JPG` : retrouvée, mais masquée dans le matériau natif

Dans le package 0.19.2 (`batch-20260915-225024`), `chair.JPG` est décodée en
512 × 512 et conservée à l'identique dans
`IR/Objects/bide.lwo/textures/2-chair.JPG`. Elle n'a aucune enveloppe animée.
Le matériau `Default` de `bide.lwo` contient deux couches couleur actives :

| Ordinal | Image | Projection | Mélange |
|---|---|---|---|
| `80` | `chair.JPG` | Planaire Z | Normal, 100 % |
| `90` | `TaurusEnvmap2.JPG` | Cylindrique Y | Normal, 100 % |

Un contrôle avec **LightWave 9.6 ScreamerNet** confirme cet ordre et son effet :
aux cinq positions testées, l'évaluation du canal couleur complet donne
exactement le résultat de Taurus seule, et diffère de chair seule.
Taurus se trouve au-dessus ; son image sans alpha, son opacité de 100 % et
son bouclage Repeat masquent chair. Le contrôle utilise une copie du LWO
dont seuls les chemins `CLIP/STIL` sont adaptés aux JPG fournis, ainsi que
des copies d'images dont les empreintes restent identiques. Le moteur termine
avec le code 0.

L'export reste limité par l'empilement des couches et par la projection
cylindrique de Taurus. **Appliquer chair seule changerait le matériau source** :
ce cas n'appelle pas le correctif d'enveloppes de `solfinal.JPG`.
Ce contrôle est un échantillonnage natif du matériau, pas une comparaison
visuelle complète ni une validation des anciens TGA absents.

[Résultats, valeurs natives et empreintes](diagnostics/flower-chair-qa-20260915.json).

## Terrain corrigé : v0.19.2

`solfinal.JPG` est désormais appliquée au matériau `terrain`, dans les exports
OBJ/glTF de l'objet et de la scène. Ses trois enveloppes de position ont deux
clés égales, des spans `LINE`, et des comportements avant/après `Linear` : leur
valeur reste constante et correspond à la position native enregistrée.
Le convertisseur qualifie ce cas sans retirer les clés ni modifier le LWO.
L'IR conserve `has_envelopes: true` et ajoute
`constant_envelopes_at_native_values: true` ; la couche passe à `approximated`.

Le [profil des enveloppes constantes](textures.md#constant-texture-envelopes)
vérifie également les interpolations, les comportements hors plage, les trois
composantes des vecteurs et l'absence de références ambiguës ou de modificateurs.
Les enveloppes variables et non qualifiées restent bloquées.

Package reconverti depuis les sources originales avec le binaire 0.19.2 :
[`output/batch-20260915-225024/packages/demo-flower-by-prune`](../output/batch-20260915-225024/packages/demo-flower-by-prune).
Il contient désormais 36 couches exportées et 33 glTF texturés. La recherche
d'images conserve les résultats précédents : 31 références décodées sur 33.

Contrôles : huit suites de régression concernées passent en Release ; la suite
des enveloppes passe aussi sous MSVC AddressSanitizer. Elle compare les pixels
et les coordonnées exportées à des versions sans enveloppes, conserve les
octets source, et vérifie les refus pour les courbes variables, les valeurs
incohérentes, les plugins et les données malformées. Les enveloppes d'un preset
restent limitées à son FORM objet embarqué.

Les 48 glTF passent la validation Khronos sans erreur ni avertissement. Blender
charge la texture du terrain en OBJ et en glTF, depuis des dossiers de format
isolés. Les deux fichiers glTF (`Objects/solfinal.lwo.gltf` et
`Scenes/solfinal.lws.gltf`) référencent effectivement leurs PNG de terrain.

[Résultats et empreintes](diagnostics/flower-texture-envelopes-20260915.json) ·
[Aperçu du terrain texturé](../_tmp/flower-textures-qa-20260915/solfinal-textured.png).
L'éclairage de cet aperçu sert au diagnostic.

## Correctif livré : v0.19.1

La recherche part maintenant du **parent du dossier de chaque LWO ou LWS** et
parcourt tous ses descendants. Les dossiers frères peuvent porter n'importe
quel nom. Les chemins relatifs explicites restent prioritaires, avec
normalisation de `.` et `..` ; les correspondances équivalentes restent ambiguës.
Chaque propriétaire détermine son propre périmètre, indépendamment de
`--content-root`, et la recherche ne remonte pas au-delà de ce parent.

Le binaire `bin/win64/lwconvert.exe` a été recompilé. Conversion depuis les
sources originales, sans copie ni déplacement d'images :

```powershell
python tools/batch_convert.py --content content --project demo-flower-by-prune --output-root output --converter bin/win64/lwconvert.exe
```

Nouveau package :
[`output/batch-20260915-220131/packages/demo-flower-by-prune`](../output/batch-20260915-220131/packages/demo-flower-by-prune).
Il retrouve **31 références d'image sur 33**, exporte 35 couches et produit
32 glTF texturés sur 48. Les deux images absentes et les limites des matériaux
décrites plus bas restent présentes.

Les 48 glTF passent la validation Khronos sans erreur ni avertissement.
Les six imports Blender (cube, ciel et panneau de crédits, en OBJ et glTF)
passent également, depuis des dossiers de format isolés. Les empreintes des
48 sources et des images archivées correspondent aux fichiers originaux.
Voir les [résultats du correctif](diagnostics/flower-textures-fixed-20260915.json).

Les tests couvrent désormais la recherche dans le parent et les sous-dossiers
frères arbitraires, les limites du parcours, les chemins relatifs avec variations
de casse, les ambiguïtés et les périmètres distincts des LWO et LWS.
Les six suites concernées passent en Release : convertisseur, batch, textures,
projections, clip maps et normal maps.

```powershell
ctest --test-dir build -C Release --output-on-failure -R "converter_regression|batch_regression|texture_regression|projection_regression|clip_maps_regression|normal_maps_regression"
```

## Diagnostic initial — v0.19.0

Le package `output/batch-20260915-212936/packages/demo-flower-by-prune`
contient 48 glTF, tous sans images. La cause principale est le **périmètre de
recherche des images**, limité au dossier du fichier propriétaire et à ses
sous-dossiers. Les images de ce projet sont dans un dossier frère :

```text
demo-flower-by-prune/
  Objects/*.lwo
  Scenes/*.lws
  Images/*.JPG
  Images/sky/*.JPG
```

[`lw_package_images`](../src/images.c) utilise `lw_dirname(source)` comme racine
et parcourt uniquement cette arborescence. Son interface ne reçoit ni
`--content-root` ni les règles `--map`. Le batch passe correctement la racine du
projet, mais celle-ci sert à résoudre les objets des scènes, sans élargir la
recherche des textures.

Cette restriction est explicite dans le code, les manifests et le test
`test_image_search_stays_within_owner_directory` de
[`tests/test_converter.py`](../tests/test_converter.py). Le comportement actuel
est donc conforme à cette règle, qui ne couvre pas cette organisation LightWave.

Exemple : `Cube.lwo` référence `C:PRUNE_Flower/Images/CUBE1_Prune.tga`.
`Images/CUBE1_Prune.JPG` existe, mais il est hors du dossier parcouru. La
substitution d'extension est déjà implémentée et fonctionne dès que l'image
devient visible. Les anciens chemins `C:PRUNE_Flower/...` ne constituent pas un
blocage supplémentaire dans cette expérience.

## Expérience avec le convertisseur inchangé

Copie du projet sous `_tmp/flower-textures-qa-20260915/content/`, puis duplication
de `Images` sous `Objects/Images` et `Scenes/Images` dans cette copie uniquement.
Conversion avec `bin/win64/lwconvert.exe`, version 0.19.0.
Les 33 références d'image appartiennent ici aux objets ; la copie sous `Scenes`
n'est pas nécessaire pour ces fichiers, dont les scènes ne déclarent aucune
référence d'image propre dans l'IR.

| Mesure | Package demandé | Expérience |
|---|---:|---:|
| Objets / scènes | 35 / 13 | 35 / 13 |
| Références d'image résolues et décodées | 0 / 33 | 31 / 33 |
| Couches de texture exportées, avec approximation | 0 / 42 | 35 / 42 |
| glTF contenant des images | 0 / 48 | 32 / 48 |
| PNG dans le dossier glTF, sous-dossiers inclus | 0 | 60 |

Les comptes de références et de couches portent sur les propriétaires IR
uniques, sans recompter leurs utilisations dans les scènes. Les 31 références
retrouvées correspondent à 27 fichiers source. Parmi elles, 23 utilisent la
substitution d'extension et 8 correspondent au nom avec son extension.

Les SHA-256 des 48 sources archivées correspondent aux originaux dans les deux
packages. Les images archivées dans l'expérience correspondent également aux
images source. Le code du convertisseur, les sources originales et le package
examiné n'ont pas été modifiés.

Package de l'expérience :
`_tmp/flower-textures-qa-20260915/output/batch-20260915-214745/packages/demo-flower-by-prune`.

## Blocages qui restent après résolution des chemins

| Objet | Cause observée |
|---|---|
| `sollw5.lwo` | `C:Flower/Images/solBAKKER.tga` : aucun fichier de même nom de base dans les images du projet. |
| `Toroid.lwo` | `C:JfyeIIDemo/Images/didiou.TGA` : aucun fichier de même nom de base. La couche vise aussi `REFL`, canal non exporté par le profil matériau actuel. |
| `bide.lwo` | Deux couches `COLR` actives à composer ; l'une utilise une projection cylindrique (`PROJ 1`) non prise en charge. Les deux images sont pourtant retrouvées. |
| `solfinal.lwo` | `solfinal.JPG` est retrouvée, mais `has_envelopes` bloque la couche. Les trois enveloppes natives `Position.X/Y/Z` ont chacune deux clés, aux temps 0 et 1, avec des valeurs identiques et des spans `LINE`. Le refus repose sur la présence des enveloppes ; il ne démontre pas une variation effective de position. |
| `sol.lwo`, `SOLuvmap.LWO` | Blocs `SHDR` conservés dans l'IR, sans évaluation du shader. |

Six images du dossier source n'ont pas de référence de même nom de base dans
l'inventaire IR : `blak.JPG`, `bois.JPG`, `CREDITS-NoRecessMODIF2.JPG`,
`MetaBoules.JPG`, `Pruner5.JPG`, `TaurusEnvmap.JPG`. Leur présence dans le dossier
ne suffit pas à déterminer leur affectation à une surface.

## Validation

- Validateur Khronos 2.0.0-dev.3.10, ressources incluses : **48 glTF, zéro erreur,
  zéro avertissement**. Les 12 messages informatifs concernent des dimensions
  d'image qui ne sont pas des puissances de deux.
- Blender 4.2.0 : imports de `Cube.lwo`, `LW_Sky.lwo` et `CREDITS_Med.lwo`, chacun
  en glTF et en OBJ, depuis des copies isolées des dossiers de format. Les six
  imports chargent les images et passent les contrôles de matériaux, UV et
  affectations aux polygones.
- [Aperçu du cube texturé](../_tmp/flower-textures-qa-20260915/cube-preview.png),
  avec éclairage de diagnostic. Ces contrôles ne mesurent pas la fidélité du
  rendu au moteur LightWave original.
- [Résultats détaillés et empreintes](diagnostics/flower-textures-qa-20260915.json),
  incluant les enveloppes natives de `solfinal.lwo`, la validation et les imports.

## Travaux distincts du correctif de recherche

Les enveloppes constantes de `solfinal.lwo` sont prises en charge depuis la
v0.19.2, après vérification de leur interpolation et de leurs comportements hors
plage. Les couches multiples, la projection cylindrique et les shaders
constituent des travaux distincts.
La duplication des images dans cette QA sert uniquement à isoler la cause.

## Reproduction

Depuis la racine du dépôt, préparer une **nouvelle** copie de travail (le chemin
ci-dessous existe déjà après cette QA ; choisir un autre nom pour rejouer) :

```powershell
@'
from pathlib import Path
import shutil
work = Path('_tmp/flower-textures-qa-20260915')
work.mkdir(exist_ok=False)
source = Path('content/demo-flower-by-prune')
target = work / 'content' / source.name
shutil.copytree(source, target)
for owner in ('Objects', 'Scenes'):
    shutil.copytree(source / 'Images', target / owner / 'Images')
'@ | python -

python tools/batch_convert.py --content _tmp/flower-textures-qa-20260915/content --project demo-flower-by-prune --output-root _tmp/flower-textures-qa-20260915/output --converter bin/win64/lwconvert.exe
```

Le batch conserve un statut `partial` : 4 conversions du sous-ensemble pris en
charge et 44 partielles, notamment à cause des limites conservées dans l'IR.
Ce statut global ne signifie pas que toutes les textures ont échoué.

Commandes de contrôle utilisées, avec le nom de batch créé lors de cette QA :

```powershell
python tests/check_gltf_validator.py _tmp/flower-textures-qa-20260915/output/batch-20260915-214745/packages/demo-flower-by-prune --validator build/tools/gltf-validator-2.0.0-dev.3.10/gltf_validator.exe --report _tmp/flower-textures-qa-20260915/gltf-validation.json

python tests/check_textures_blender.py --project _tmp/flower-textures-qa-20260915/output/batch-20260915-214745/packages/demo-flower-by-prune --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe" --asset Objects/Cube.lwo --asset Objects/LW_Sky.lwo --asset Objects/CREDITS_Med.lwo --report _tmp/flower-textures-qa-20260915/blender-import.json --preview _tmp/flower-textures-qa-20260915/cube-preview.png

python documentation/diagnostics/analyze_flower_textures.py --source content/demo-flower-by-prune --baseline output/batch-20260915-212936/packages/demo-flower-by-prune --experiment _tmp/flower-textures-qa-20260915/output/batch-20260915-214745/packages/demo-flower-by-prune --converter bin/win64/lwconvert.exe --validator-report _tmp/flower-textures-qa-20260915/gltf-validation.json --blender-report _tmp/flower-textures-qa-20260915/blender-import.json --report documentation/diagnostics/flower-textures-qa-20260915.json
```
