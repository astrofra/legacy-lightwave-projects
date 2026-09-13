# Normal maps — v0.13.0

Le convertisseur réencode désormais les normal maps objet en normal maps
tangentes PNG, en C autonome, sans LightWave et sans subdivision du maillage.
Le premier profil couvre le shader **NormalShader** de MicroWave, notamment
`content/collosus-concept-design/items/aircon`.

## Utilisation

`convert_content.bat` active cette conversion par défaut, avec une détection
prudente de l'espace source. Aucun preset n'est enregistré. Le binaire distribué
est `bin/win64/lwconvert.exe`.

```powershell
.\convert_content.bat --file collosus-concept-design/items/aircon/aircon.lwo
```

Les mêmes options existent dans le CLI C et dans le batch :

| Option | Effet |
|---|---|
| `--normal-space auto` | Hypothèse objet uniquement si les mesures sont suffisamment discriminantes ; sinon abstention. Défaut. |
| `--normal-space object` | RGB représente les axes XYZ de l'objet LightWave. |
| `--normal-space world` | RGB représente des normales monde ; exige la matrice du bake d'origine. |
| `--normal-world-matrix a,b,c,d,e,f,g,h,i` | Partie linéaire 3×3 objet→monde du bake, coefficients par lignes, séparés par des virgules. Valide uniquement avec `world`. |
| `--normal-space tangent` | L'utilisateur affirme que la carte emploie déjà la base tangente finale compatible MikkTSpace, avec bitangente V montant. |
| `--normal-green negative` | Inverse la composante verte source ; défaut `positive`. |
| `--normal-space off` | Préserve les données et l'image sans produire de normal map dérivée. |

Exemple monde, avec une matrice **connue** et une échelle non uniforme :

```powershell
.\bin\win64\lwconvert.exe convert chemin\objet.lwo --output build\normal-world --normal-space world --normal-world-matrix "2,0,0,0,1,0,0,0,0.5"
```

La matrice actuelle d'une instance LWS n'est pas automatiquement considérée
comme celle du bake. Les matrices singulières, non finies ou très mal
conditionnées sont refusées. Une base tangente historique inconnue ne peut pas
être rendue compatible simplement en recalculant les tangentes : le mode
`tangent` est une affirmation explicite, pas une reconnaissance de cette base.
Dans le batch, si le premier coefficient est négatif, employer la forme
`--normal-world-matrix="-1,0,0,0,1,0,0,0,1"` pour éviter qu'argparse le prenne
pour une nouvelle option. Le CLI C accepte sa valeur séparée habituelle.

## Extraction et préservation

Le lecteur reconnaît une sérialisation bornée : `NSNS`, `NSNO`, le VParm vectoriel
`VPVL/VPRM` de type 3, flags 1, ses valeurs statiques, un seul `TBLK/BLOK/IMAP`,
un `ICNT` égal à 1 et une seule image `IMGS/CLIP/STIL`. Les conteneurs privés sont
parcourus dans leurs limites, avec des champs connus et uniques. Les attributs
IMAP suivent le lecteur LWO2 existant et la qualification du profil UV ; les
paramètres non pris en charge restent signalés comme non interprétés.

L'image privée reçoit un `clip_scope` distinct des CLIP globaux : zéro pour les
CLIP globaux, offset du SHDR + 1 pour le shader. L'IMAG privé et la seule image
privée sont associés sans supposer que leur numéro vaut 1 ou correspond à une
image globale. Le résolveur existant retrouve ensuite les JPG remplaçant les TGA.

L'IR conserve le SHDR, le BLOK image extrait, le champ numérique `NSNS`, le
chemin original, la résolution et ses candidats, le hash de l'image, ses octets
originaux et la source LWO complète. **La signification numérique de `NSNS`
n'est pas déduite de l'ordre des chaînes dans le plugin.** Le champ n'est pas
utilisé comme preuve de l'espace ; cet espace vient de l'heuristique ou du CLI.

`materials[].derived_maps.normal` référence le PNG produit. Le BLOK `NORM`
contient `normal_conversion` : profil, espace demandé/retenu, matrice éventuelle,
convention verte, mesures de détection, couverture, recouvrements, conflits,
vecteurs invalides, UV dégénérés, padding et erreur de quantification.
Le statut `approximated` est conservé : ce n'est pas une émulation complète du
shader MicroWave. Les tangentes dérivées sont dans le glTF ; la géométrie et
les maps natives de l'IR ne sont pas remplacées.

## Détection automatique

L'heuristique n'inspecte que les images effectivement liées au NormalShader.
Elle ne transforme pas une texture parce que son nom contient « normal ».
Elle échantillonne les intérieurs des triangles UV, à au moins 3 % des arêtes,
sans compter plusieurs fois un texel couvert. Les vecteurs RGB sont décodés
linéairement, sans transfert sRGB. Les longueurs hors `[0,7 ; 1,3]` sont écartées
pour la détection.

Le profil actuel exige simultanément :

- au moins 128 échantillons valides, représentant au moins 90 % des échantillons ;
- une diversité des normales géométriques supérieure à 0,2
  (`1 − longueur de leur moyenne`) ;
- un angle moyen RGB→XYZ / normale géométrique inférieur à 40° ;
- plus de 10° d'avance sur la meilleure des 47 autres permutations/signes XYZ ;
- plus de 95 % de vecteurs dans l'hémisphère de la normale géométrique ;
- moins de 90 % de vecteurs à Z positif, contrairement à une carte tangente usuelle.

Ce sont des seuils conservateurs de décision, **pas des probabilités**. Les
permutations alternatives servent de contre-hypothèses ; elles ne sont pas
appliquées silencieusement. Une carte tangentielle, une surface plane, des axes
inconnus ou un signal peu discriminant provoquent une abstention. Une carte
monde alignée avec l'objet peut être indiscernable d'une carte objet : l'IR
qualifie donc le résultat automatique d'« object-aligned hypothesis ».

## Base tangente et réencodage

[MikkTSpace](https://github.com/mmikk/MikkTSpace/tree/3e895b49d05ea07e4c2133156cfa94369e19e409)
est vendoré sans modification, avec son attribution à Morten S. Mikkelsen et
sa licence dans `third_party/mikktspace/` et `bin/win64/licenses/`.

Le baker et le glTF partagent la construction des triangles, les normales
natives explicites ou lissées, les coutures VMAD et la conversion d'axes.
MikkTSpace travaille par coin de triangle ; les résultats ne sont pas moyennés
avec les anciens indices de points. Les triangles dérivés utilisés pendant le
bake sont réutilisés à l'export, y compris pour les couches et les skins.

Les UV stockés dans le glTF ont V descendant. Le signe retourné par MikkTSpace
sur ces coordonnées est inversé pour exporter explicitement une bitangente
V montant, compatible avec les normal maps usuelles et l'importeur Blender.
`TANGENT` contient XYZ et ce signe W ; `B = W × cross(N,T)`. Cette convention
est appliquée à la fois au bake et au fichier final. Le validateur structurel
ne détecte pas une inversion de ce signe : la comparaison de rendu ci-dessous
sert aussi à la vérifier.

Au centre de chaque texel couvert, le baker interpole T, B et N, normalise
chaque colonne, puis inverse la matrice complète. Les colonnes interpolées
ne sont pas nécessairement orthogonales ; une transposition ne suffirait pas.
Pour une source monde, la normale objet est `normalize(Aᵀ × n_monde)` avant
le changement d'axes, où A est la matrice objet→monde du bake.

Le PNG garde la résolution source et des composantes RGB linéaires. Le glTF
contient `normalTexture` et `TANGENT`, sans extension supplémentaire. Quatre
anneaux de padding recopient des texels voisins sans moyenner des vecteurs
opposés ; les texels non couverts restent neutres. Il n'y a pas de nouvelle
compression JPEG ni de nouvelle subdivision.

## Limites explicites

Le profil traite une image UV répétée, opaque, sans animation ou transformation
de mapping et avec la même map UV que les textures de couleur existantes.
Il refuse les empilements de normal maps, les projections non UV, les images
transparentes nécessitant le compositing du VParm, les UV dégénérés et les
recouvrements demandant des vecteurs tangents différents de plus de 3°.
Les répétitions hors `[0,1]`, y compris négatives, sont rastérisées ; des
recouvrements identiques sont permis. Aucun nouvel atlas n'est inventé.

Le budget est de 16 mégapixels et 128 millions de positions de raster par passe,
avec un plafond de 64 millions pour une boîte de triangle. Un dépassement
préserve la source et explique l'abstention. Ces limites bornent le bake,
notamment pour les UV très répétés.

Le filtrage entre texels, les coutures, les bases interpolées des moteurs,
les mipmaps et la quantification peuvent produire des écarts. La mesure de
reconstruction interne ne constitue pas une mesure de fidélité à LightWave.
OBJ/MTL n'a pas de liaison normal map tangente portable dans son profil de base :
le convertisseur ne déguise pas une normal map en `bump` de hauteur. La carte
reste dans l'IR et est liée en glTF. FPrime reste non évalué.

## Validation aircon

Batch obtenu avec le `.bat` et le binaire distribué :
`build/aircon-normal-final/batch-20260913-135533`.

- `aircon_norm.tga` retrouve `aircon_norm.jpg` dans la table privée du shader.
- L'auto retient l'hypothèse objet : 133 099 échantillons ; angle moyen 33,63°,
  meilleure permutation concurrente 63,23°, hémisphère géométrique 100 %.
- 161 123 texels couverts, aucun conflit de recouvrement ; 27 150 texels de padding.
- Erreur maximale de reconstruction du PNG quantifié : 0,47° environ.
- Les trois glTF publiés (`aircon` objet/scène et `tv_small` dépendant) passent
  le validateur Khronos sans erreur ni avertissement ; liens et hashes vérifiés.

Le contrôle indépendant `tests/check_normal_maps_blender.py` compare, dans
Blender 4.2, les normales tangentes du glTF importé avec la JPG originale évaluée
en espace objet. Sur 138 226 pixels visibles intérieurs d'une vue : moyenne
**3,25°**, médiane **2,26°**, percentile 95 **8,32°**, percentile 99 **19,80°**,
maximum **59,41°**. Cela valide une approximation utile sur cette vue ; cela ne
prouve ni une égalité pixel à pixel ni l'émulation du shader LightWave. Les
écarts extrêmes restent une limite à réduire, notamment par un travail sur le
filtrage et les bordures d'îlots. Le seuil QA retenu pour cette approximation
est médiane < 3°, moyenne < 5° et percentile 95 < 10°.

Les mesures historiques de l'étude exploratoire excluaient les deux triangles
hors du carré UV et utilisaient un masque d'échantillonnage différent : leurs
comptages et angles ne doivent pas être confondus avec ceux de ce baker.

Les tests synthétiques vérifient une surface +X, les UV miroir/négatifs, des
normales lissées, une matrice monde avec échelle/cisaillement, l'abstention, les
payloads mal formés, les collisions CLIP privées/globales, les recouvrements,
les budgets et les accessors d'un skin animé avec tangentes.
Voir les [résultats enregistrés](diagnostics/aircon-normal-maps-qa.json).

```powershell
python -X utf8 tests/check_normal_maps_blender.py --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe" --gltf build/aircon-normal-final/batch-20260913-135533/packages/collosus-concept-design/gltf/items/aircon/aircon.lwo.gltf --source-image content/collosus-concept-design/items/aircon/aircon_norm.jpg --report build/aircon-normal-final-reference.json
```
