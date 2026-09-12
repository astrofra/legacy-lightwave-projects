# Normales et lissage — v0.8.0

QA du 12 septembre 2026. L'audit a confirmé que l'IR conservait les données
natives, mais que les exports les exploitaient insuffisamment : normales plates
par triangle en glTF, absence de `vn` et de `s` en OBJ. La version 0.8.0 ajoute
un calcul commun par coin source dans `src/normals.c`, utilisé par les deux
exporteurs. Les normales des animations évaluées sont également capturées.

## Données conservées et interprétation

| Donnée LightWave | IR | OBJ / glTF |
|---|---|---|
| `SURF/SMAN` | `materials[].smoothing_angle`, valeur originale en radians ; bit de présence distinct de zéro | Normales par coin selon le seuil angulaire |
| `LWOB/SURF/FLAG`, bit 4 | `materials[].flags`, valeur brute | Angle par défaut de 1,56207 rad si le bit est actif et `SMAN` absent |
| Source de surface LWO2 | `source_name` et octets d'origine | Héritage du seul angle de lissage ; source absente ou cycle signalés |
| `PTAG/SMGP` | `tag_assignments`, type numérique et `type_name`, bloc de polygones, polygone, index dans `tags` | Frontières de groupes respectées dans les normales ; identifiants `s` en OBJ |
| `VMAP/NORM` | Nom, dimension, bloc de points, entrées et valeurs float32 originales dans `geometry.bin` | Normales explicites continues |
| `VMAD/NORM` | Même structure, avec identité du polygone et du point | Priorité sur `VMAP` pour les coins concernés |

Le nouvel objet `materials[].smoothing` précise l'unité, la présence du champ,
l'activation et l'angle effectif, ainsi que leur origine. `shading` donne le
profil, les nombres de coins explicites/lissés et les diagnostics. Ces ajouts
ne remplacent aucun champ natif. Les normales explicites sont normalisées
uniquement dans les dérivés ; leur magnitude originale reste dans l'IR.
`source.bin` reste une copie identique de l'entrée. Le layout de `geometry.bin`
n'est pas modifié par les normales calculées.

Les types natifs et les identités de points restent distincts des sommets de
rendu. Deux points de coordonnées identiques ne sont jamais soudés par ce
calcul. Une coupure UV ou une séparation en matériaux ne change pas l'identité
du coin source et ne crée pas automatiquement une coupure de lissage.

## Calcul des normales

Le profil `source-corner-normals-0.1` procède avant triangulation :

1. Calculer une normale unitaire par polygone source, depuis ses première et
   dernière arêtes. Pour un départ collinéaire, réflexe ou une frontière avec
   pont, utiliser la somme orientée de toute la frontière ; compter ce recours.
2. À chaque coin sans normale explicite, partir de la normale du polygone.
   Si son angle effectif est positif, ajouter les normales unitaires des faces
   partageant le même point source et le même groupe de lissage.
3. Chaque face voisine doit elle-même activer le lissage. Le seuil est celui
   de la face propriétaire du coin. Deux angles différents peuvent donc
   produire des normales différentes de part et d'autre d'une frontière.
4. Normaliser la somme. Le poids est égal par **polygone source**, sans
   pondération par aire ni par nombre de triangles produits. L'opération ne
   propage pas transitivement le lissage à travers une chaîne de faces.

Les faces sans `SMGP` partagent un groupe implicite distinct des groupes
explicitement nommés, y compris le tag zéro. Cette convention est documentée
pour les assignations partielles ; aucun exemple réel de `SMGP` n'a été trouvé
dans l'inventaire utilisé ici. Un groupe explicite n'active pas à lui seul un
angle de lissage nul.

Une seule carte `NORM` de dimension 3 et de nom non ambigu est sélectionnée par
bloc de points. `VMAP` et `VMAD` du même nom constituent une carte. Plusieurs
noms concurrents, références invalides, dimensions incompatibles, vecteurs
nuls ou non finis sont signalés ; les coins concernés utilisent le calcul
géométrique disponible. Il n'y a pas encore de sélecteur de carte par surface
pour les variantes plus récentes de LightWave.

Les sources de cette implémentation indépendante sont la documentation
[LWO2 du SDK NewTek](https://documentation.help/LightWave/lwo2.html), les exemples
`sample/Utility/lwobject/pntspols.c` et `lwob.c` du SDK fourni par l'utilisateur,
et les mesures natives décrites ci-dessous. Aucun code de ces exemples SDK
n'a été copié ou incorporé dans le convertisseur autonome.

## Traduction dans les formats de sortie

**OBJ** écrit une normale `vn` par coin source, référencée par les faces sous
la forme `v/vt/vn` ou `v//vn`. Les snapshots de scènes appliquent l'inverse
transposée de la transformation à la normale, puis la réflexion de Z. Les
échelles non uniformes et négatives sont couvertes. Une transformation
singulière bloque le snapshot de scène avec un diagnostic ; les objets et
l'IR restent exportables.

Les commandes `s off`, `s 1` pour le groupe implicite et `s <tag+2>` pour les
groupes explicites accompagnent ces normales. Un commentaire garde l'index
de tag original. Les `vn` portent le résultat angulaire complet : un seul
entier `s` par face ne peut pas exprimer toutes les discontinuités par coin.
Un logiciel qui ignore les `vn` et recalcule le lissage peut donc diverger.

**glTF** écrit l'attribut standard `NORMAL`, avec duplication des sommets de
rendu aux coins déjà utilisée pour les UV et la provenance. Les valeurs
brutes et effectives de l'angle figurent aussi dans les `extras` des matériaux.
Le format n'a pas besoin d'une extension pour ces normales ; les groupes et
les angles sont interprétés en valeurs par sommet. Les paramètres natifs
complets restent dans l'IR, auquel les `source_map` relient les coins.
Voir la [spécification glTF 2.0](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html).

Les cages de subdivision restent des cages. Cette correction ne bake aucune
subdivision et ne modifie pas les polygones de l'IR. Les cages dégénérées
peuvent ne pas posséder de normale géométrique définie ; ce cas est signalé.

## Animation évaluée et limites de l'oracle

`lw_capture.p` écrit maintenant le protocole `LWCONVERT_CAPTURE 2`. Les records
`V polygon corner point nx ny nz` conservent la normale **évaluée dans le monde**
renvoyée par `LWMeshInfo.pntOtherNormal`. La provenance, les identités de points
et les frontières complètes des polygones sont vérifiées avant export.
La capture reste à côté des clés et contraintes natives, sans les remplacer.

L'exporteur ramène ces normales dans l'espace local du maillage avec la
transposée de la matrice objet, puis écrit le `NORMAL` initial et les deltas
`NORMAL` de chaque morph target. Les normales aux images capturées sont ainsi
préservées. L'interpolation entre images reste celle du glTF ; une fidélité
continue entre échantillons n'est pas revendiquée.

Les captures du protocole 1 restent lisibles. Leur reconstruction de normales
plates est seulement admise pour des triangles sans lissage ni carte de
normales. Une animation lissée ou avec n-gones demande une nouvelle capture,
avec un message explicite. Aucun ancien cache n'est réécrit automatiquement.

Les dix sondes avec LightWave 9.6 AMD64, build 1539, donnent :

- Accord à moins de `2 × 10⁻⁸` sur le lissage à poids égal, les seuils, les
  deux surfaces lissées, les angles différents et le défaut du flag LWOB.
- Une surface voisine dont le lissage est désactivé est exclue. Cette mesure
  complète la règle de l'exemple SDK, qui ne faisait pas cette exclusion.
- Sur nos fixtures, l'API de ce moteur n'applique pas les `SMGP` distincts ni
  la carte `NORM` isolée comme l'interprétation de fichier choisie pour les
  exports statiques. L'export d'animation évaluée avec `NORM` ou `SMGP` est donc
  déclaré non qualifié et refusé avec diagnostic. Les dérivés de repos et les
  données natives restent disponibles.
- Avec une échelle non uniforme, le moteur moyenne des normales de faces
  déjà évaluées dans le monde. Cela diffère de l'inverse transposée d'une
  moyenne calculée dans l'objet. L'export animé conserve le résultat capturé ;
  le glTF statique suit sa transformation normale standard. Cette différence
  avec LightWave est mesurée et reste explicite.

## Résultats de QA

Les données détaillées et les empreintes sont dans
[normals-qa.json](diagnostics/normals-qa.json). L'
[inventaire source](diagnostics/normals-inventory.json) est un instantané de
1 192 objets LWOB/LWO2 : 840 avec `SMAN`, 791 avec un angle positif et 456 avec
le flag LWOB de lissage. Aucune carte `NORM` ni assignation `SMGP` n'a été
trouvée ; leurs tests sont donc synthétiques. Cet instantané précède l'ajout
d'autres projets au répertoire `content` et ne prétend pas inventorier ces ajouts.

La reconversion couvre **166 objets** : Aliens, Butterfly Tank, Orange Juice,
Smila, Carrot Robot et Redline. Elle compare 1 015 368 coins glTF, vérifie les
`vn` OBJ et confirme la conservation des sources. L'écart maximal entre les
normales présentes dans les deux dérivés est `3,46 × 10⁻⁸` ; l'erreur maximale
de longueur unitaire glTF est `4,82 × 10⁻⁸`. 229 125 triangles ont des normales
variables entre leurs coins, ce qui confirme que la sortie n'est plus
systématiquement plate.

Deux objets, `light_1.lwo` et `light_2.lwo` de Carrot, conservent chacun dix
faces de cage dégénérées sans normale définie : le manifeste compte ces
problèmes. 224 coins de cages OBJ n'ont pas de triangle glTF correspondant,
en raison du nettoyage ou du rejet de leurs contours par la triangulation.
Ces limites ne sont pas masquées par la comparaison des coins communs.

Le validateur Khronos `2.0.0-dev.3.10` donne **zéro erreur, zéro avertissement**
sur les 166 glTF d'objets et les quatre glTF du package Smila animé. Les
**118 tests de régression, répartis en huit suites**, passent en Release et
sous MSVC AddressSanitizer.

Blender 4.2 importe les quatre fixtures OBJ/glTF (lisse, dur, groupes distincts,
normales explicites) et deux objets réels (`oj_tv.lwo`, `mesh_bot_body.lwo`).
La comparaison porte sur les normales réellement exposées par les coins de
maillage après import. Les écarts maximaux sont d'environ `5,7 × 10⁻⁵` sur les
fixtures et `0,00288` sur les objets réels, soit environ 0,165 degré. L'import
Blender n'est donc pas une conservation bit à bit ; les valeurs des fichiers
OBJ/glTF concordent beaucoup plus étroitement. Le contrôle accepte une
différence vectorielle maximale de 0,005 et enregistre le pire cas.

Enfin, `smila_run_cycle.lws` est réévalué sur les images 0 à 25. Aux images
0, 13 et 25, 13 242 normales par image ont été comparées indépendamment aux
captures natives, après transformation dans le monde. L'écart maximal est
`1,35 × 10⁻⁷`. 2 342 normales changent entre la première image et les images
13/25. Les limites de mouvement du fichier restent celles documentées dans
la [QA Smila](smila-animation-qa.md).

## Reproduction

Depuis la racine du dépôt, avec des répertoires de sortie nouveaux :

```powershell
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
python -X utf8 documentation/diagnostics/qa_normals.py --output output/normals-corpus-check
python -X utf8 tests/check_normals_oracle.py --output output/normals-oracle-check --lightwave-root _tmp/_extern/LightWave/LW9.6
python -X utf8 tools/export_lightwave_animation.py content/smila-by-moebius/smila_run_cycle.lws --content-root content/smila-by-moebius --output output/normals-smila-check --lightwave-root _tmp/_extern/LightWave/LW9.6 --start-frame 0 --end-frame 25
python -X utf8 tests/check_animated_normals.py output/normals-smila-check --report output/normals-smila-check/normals-check.json
```

`tests/check_normals_blender.py` documente la commande Blender en arrière-plan
pour vérifier une liste d'OBJ/glTF statiques. Le convertisseur et le helper
Release actualisés sont copiés dans `bin/win64/` par CMake.
