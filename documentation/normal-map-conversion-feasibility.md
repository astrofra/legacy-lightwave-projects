# Conversion des normal maps anciennes vers glTF — faisabilité

Étude du 12 septembre 2026, à partir d’`aircon` et du convertisseur 0.12.0.

**La démarche est faisable en C autonome.** Une carte contenant les normales
en espace objet peut être réencodée dans l’espace tangent du maillage exporté.
Il suffit de disposer de la correspondance UV, des normales de shading et du
repère tangent à chaque point de la surface. Le modèle haute résolution ayant
servi au baking n’est pas nécessaire : son information de normales est déjà
dans l’image. Cette conversion ne reconstruit ni son relief géométrique ni sa
silhouette, et ne demande pas de subdiviser le modèle.

« World map » est interprété ici comme **carte de normales en espace monde**.
Une carte de positions monde serait un autre problème. Objet, monde et tangent
sont trois espaces possibles pour une normal map, pas trois types d’images que
leur extension permettrait d’identifier.

| Espace source | Information nécessaire | Faisabilité |
|---|---|---|
| Objet | Maillage, UV, conventions d’axes et d’encodage | Bonne, sans scène si le mapping appartient au LWO |
| Monde | Les mêmes données et la transformation objet→monde utilisée lors du bake | Bonne si cette transformation est connue ; ambiguë sinon |
| Tangent | La convention de canaux et le repère tangent du baker d’origine | Réutilisable si compatible ; une ancienne base inconnue ne devient pas MikkTSpace simplement en recalculant les tangentes |

Le champ `normalTexture` de glTF utilise déjà l’espace tangent, avec des
composantes RGB linéaires. Aucune extension n’est nécessaire pour la carte.
`TANGENT` porte la direction XYZ et un signe W permettant de reconstruire la
bitangente. La spécification recommande MikkTSpace lorsque les tangentes sont
absentes ; pour une carte réencodée, nous exporterions les tangentes explicitement
afin de conserver celles utilisées pendant le calcul.
[Spécification glTF, géométrie](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#meshes),
[normal textures](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#additional-textures).

**Ce qu’`aircon` permet déjà de vérifier.** Son LWO ne contient que les maps
`TXUV aircon_uv`, `MORF unfold_uv` et une map discontinue `TXUV aircon_uv`.
Aucune tangente/bitangente explicite n’y a été trouvée. Cela ne prouve pas leur
absence dans tous les dialectes LWO : le format permet des types de maps
supplémentaires. Le lecteur conserve ces données même lorsqu’elles ne sont pas
interprétées. Voir la [description des maps du SDK](../extern/lwsdk-master/html/filefmts/lwo2.html#c_VMAP).

Le plugin `NormalShader.p` et le manuel **MicroWave 1.0**, Evasion Ltd., sont
présents dans les archives fournies. Le manuel décrit l’encodage des normales
objet ou tangentes dans RGB, recommande l’espace objet pour son workflow de
simulation et fournit une scène d’exemple. Le binaire contient les libellés
`Normal Space`, `Object` et `Tangent`. Le champ privé `NSNS` vaut zéro dans
`aircon.lwo` et dans `head_normal_shader.lwo` de ce tutoriel. **L’association
numérique zéro→objet reste à confirmer** par une sauvegarde contrôlée des deux
modes ; le nom d’un champ et l’ordre de chaînes dans un binaire ne suffisent pas.
Aucun plugin n’a été exécuté pour cette étude.
[Manuel local](../_tmp/_extern/newtek_full/add-on/%23layout/MicroWave_pdxe3dm/manual/index.html).

La texture privée du shader pointe vers `aircon_norm.tga`, disponible sous le
nom `aircon_norm.jpg`, avec le mapping `aircon_uv`. Son image et son CLIP sont
dans le payload du plugin : il faudra les extraire dans un espace d’identifiants
distinct du tableau CLIP de l’objet, puis employer le résolveur d’images existant.

Un [script de diagnostic](diagnostics/analyze_normal_map_space.py) compare cette
image aux normales lissées effectivement exportées, en annulant la réflexion Z
du convertisseur. Il échantillonne les intérieurs UV du matériau `aircon_target`
et teste les 48 permutations/signes des trois composantes. Ce script Python
(NumPy/Pillow) sert seulement à l’étude ; il n’est pas une dépendance du C.

| Mesure | Résultat |
|---|---:|
| Taille de l’image | 512 × 512 |
| Triangles du matériau | 213 |
| Triangles UV dégénérés | 0 |
| Triangles dépassant [0,1], exclus de cette sonde | 2 |
| Texels intérieurs couverts, hors triangles exclus | 147 053 |
| Chevauchements de ces texels intérieurs | 0 |
| Échantillons retenus pour le score d’orientation | 121 406 |
| Meilleure convention objet | RGB→XYZ, aucun signe inversé |
| Écart angulaire moyen / médian avec les normales lissées | 34,15° / 34,01° |
| Écart moyen du deuxième candidat | 56,04° |
| Vecteurs dans l’hémisphère de la normale lissée | 100 % des échantillons retenus |
| Vecteurs à Z positif si lus comme une carte tangentielle classique | 55,31 % |

Ces résultats, le manuel et l’aspect de l’image **favorisent une carte alignée
sur l’espace objet**. L’écart de 34° n’est pas une erreur mesurée du convertisseur :
les normales de détail n’ont pas à coïncider avec celles du maillage simplifié.
Le score ne démontre pas la fidélité au rendu LightWave. Une carte monde bakée
avec une transformation équivalente reste indiscernable. La sonde exclut une
bordure barycentrique de 3 % et 509 vecteurs dont la longueur décodée est hors
(0,7 ; 1,3) ; elle ne vérifie donc ni toutes les coutures ni les deux triangles
hors domaine. [Mesures complètes et hashes](diagnostics/aircon-normal-space-analysis.json).

**Calcul envisagé.** Décoder chaque texel utile comme un vecteur, sans correction
sRGB : `n = normalize(2 × RGB − 1)`, après application de la convention de canaux
identifiée. Si la normale est en monde et que `A` est la partie linéaire de la
transformation objet→monde du bake, revenir à l’objet avec
`n_obj = normalize(transpose(A) × n_world)` : c’est l’inverse de la transformation
des normales `inverse(transpose(A))`, pas la transformation des positions.
Les transformations singulières doivent être signalées.

Rasteriser ensuite les **triangles réellement exportés** dans le domaine UV.
Chaque texel couvert reçoit des coordonnées barycentriques et le repère
interpolé `F = [T B N]` correspondant à ce point. Tous les vecteurs doivent
être exprimés dans le même espace. Le réencodage est :

```text
n_tangent = normalize(inverse(F) × n_obj)
RGB_sortie = 0.5 × (n_tangent + 1)
```

Dans une base orthonormale, cette opération se réduit à trois produits
scalaires avec T, B et N. Une base interpolée n’est toutefois pas forcément
orthonormale : l’inverse doit correspondre au chemin exact de reconstruction
du shader cible. Il ne faut ni soustraire simplement la normale lissée, ni
normaliser/orthogonaliser arbitrairement les vecteurs entre bake et rendu.
Le header de référence détaille cette contrainte ainsi que le traitement du
signe de bitangente et des coins de faces.
[MikkTSpace, interface et contrat du sampler](https://github.com/mmikk/MikkTSpace/blob/master/mikktspace.h).

MikkTSpace est bien la bibliothèque évoquée : deux fichiers C autonomes,
`mikktspace.c` et `mikktspace.h`, par Morten S. Mikkelsen. Elle reçoit positions,
normales et UV par callbacks et produit les tangentes par coin. Le vendoring
proposé conserverait ces fichiers à une révision figée avec leurs hashes,
leur notice et l’attribution dans `third_party/README.md` et les licences du
binaire. La licence des sources est de type zlib, sans recours au GPL ; toute
modification locale doit être identifiée. Elle ne fournit ni lecteur d’images,
ni identification de l’espace, ni rasteriseur de baking.
[Sources et notice](https://github.com/mmikk/MikkTSpace/blob/master/mikktspace.c).

**L’heuristique doit pouvoir répondre « indéterminé ».** Donner priorité à une
sémantique de plugin vérifiée, puis comparer des hypothèses sur les zones
couvertes du mesh : longueurs des vecteurs avant normalisation, proximité du
pôle +Z, corrélation avec les normales lissées, diversité des orientations et
cohérence sur les îlots. Les pixels de fond et les bords JPEG doivent être
distingués des données utiles. Une image violette n’est qu’un indice : une
surface presque plane peut rendre les hypothèses objet et tangent équivalentes.
Un ajustement de rotation peut proposer une orientation monde, mais ne récupère
pas de manière certaine une transformation de bake perdue.

Les scores et les hypothèses concurrentes iraient dans l’IR, avec la provenance
du choix. Un choix explicite à la ligne de commande permettrait de lever les
ambiguïtés, sans sauvegarde automatique de presets. Ce sont des options à
concevoir, pas des options déjà disponibles.

Les principales difficultés sont concrètes :

- **UV superposés ou répétés** : un texel source peut être utilisé avec plusieurs
  repères tangents et demander plusieurs couleurs de sortie. Accepter seulement
  des demandes compatibles ; sinon signaler le conflit. Un nouvel atlas est
  une opération distincte, pas un changement implicite du maillage.
- **Coutures, lissage et triangulation** : réutiliser les normales par coin de
  `src/normals.c` et les triangles du writer. Les normales de faces seules
  ne conviennent pas. Ne pas fusionner les coins dont la tangente ou le signe
  diffère, même si leur position est identique.
- **Conventions** : la réflexion de Z et le changement `v → 1−v` existent déjà
  dans `src/gltf.c`. Calculer les tangentes sur les attributs finaux et transformer
  les normales source dans le même espace évite des inversions ajoutées au hasard.
  Une tangent map d’origine pourrait demander un véritable changement de base.
- **Échantillonnage** : ajouter une marge autour des îlots, éviter le mélange de
  vecteurs d’îlots différents, puis vérifier le filtrage et les mipmaps. Un PNG
  prévient une nouvelle perte, sans réparer les erreurs déjà présentes dans le
  JPEG. Une précision plus élevée en travail réduit les pertes intermédiaires.
- **Instances et animation** : une carte monde suppose une instance et une pose
  de bake identifiées. Le réencodage objet→tangent convient au skinning standard,
  sans garantir le comportement d’un shader LightWave sous déformation. Une
  subdivision activée au bake peut aussi différer du maillage de base conservé.

Le travail le plus conséquent sera le rasteriseur UV et sa gestion des
ambiguïtés, pas la multiplication par une matrice. La préparation actuelle des
textures précède la triangulation du writer : il faudra partager cette étape
de géométrie entre le baking et l’export, plutôt que calculer deux triangulations
ou deux jeux de normales indépendants. Les dérivés seront attachés à l’objet,
au mapping, aux normales et au profil de tangentes ; le seul hash de l’image
source ne suffit pas comme clé de cache.

La roadmap proposée garde chaque étape vérifiable :

| Étape | Livraison | Critère d’acceptation |
|---|---|---|
| 1. Profil NormalShader | Extraction bornée de `NSNS`/`NSNO`, image privée, UV et archivage de la source | Sauvegardes contrôlées objet/tangent ; aucune collision avec les CLIP globaux |
| 2. Tangentes C | MikkTSpace vendoré, adaptateur par coin et `TANGENT` glTF | Plan, cube lissé, couture UV, îlot miroir et changement de repère ; signes et bases vérifiés |
| 3. Bake explicite objet→tangent | Rasteriseur UV, PNG dérivé et `normalTexture` | Sur fixtures, reconstruire la normale objet depuis le résultat et mesurer l’erreur angulaire ; cas dégénérés et conflits rapportés |
| 4. Qualification aircon | Même comparaison sur tout l’atlas, répétitions et coutures incluses | Inspection sous éclairage mobile dans Blender et un second renderer glTF ; distinction des limites du shader |
| 5. Détection automatique et monde | Scores, abstention et transformation de bake explicite | Corpus de cas ambigus, instance tournée, échelle non uniforme et provenance conservée |

Pour les fixtures synthétiques valides, viser d’abord moins de 0,01° d’erreur
angulaire avant quantification, puis un P95 inférieur à 1° avec PNG 8 bits dans
les intérieurs bien conditionnés. Ces valeurs sont des **objectifs à mesurer**,
pas des résultats obtenus. Les bords, pixels non couverts et conflits doivent
avoir des métriques séparées. Une reconstruction par notre propre inverse
vérifie l’algèbre ; une comparaison de rendu indépendante reste nécessaire pour
déceler une différence de convention de shader.

Cette passe livre l’étude et les mesures. Le convertisseur et les binaires ne
sont pas modifiés ; aucune normal map dérivée ni tangente MikkTSpace n’a été
produite. Le premier chantier recommandé est le profil explicite **objet→tangent
sur aircon**, avant une détection automatique générale.

Reproduction de la sonde sur la conversion de référence :

```powershell
python -X utf8 documentation/diagnostics/analyze_normal_map_space.py --gltf build/aircon-textures-qa/batch-20260912-224012/packages/collosus-concept-design/gltf/items/aircon/aircon.lwo.gltf --image content/collosus-concept-design/items/aircon/aircon_norm.jpg --material aircon_target --report documentation/diagnostics/aircon-normal-space-analysis.json
```
