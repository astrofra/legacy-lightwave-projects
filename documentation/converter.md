# Convertisseur LWS/LWO en C — v0.1.0

État au 10 septembre 2026. Ce premier jalon fournit une bibliothèque C17 et un
exécutable `lwconvert`, sans dépendance à Blender. Il extrait les structures
natives et produit des OBJ/MTL. Les backends glTF 2.0 et `.blend` restent à écrire.

## Commandes

La compilation Windows et les exemples de démarrage figurent dans le
[README](../README.md). MSVC 19.41 x64 a été utilisé en Debug, Release et
RelWithDebInfo avec AddressSanitizer. La branche POSIX existe, mais sa compilation
et son comportement n'ont pas encore été validés sur Linux/macOS.

```text
lwconvert inspect INPUT
lwconvert convert INPUT --output NEW_DIRECTORY
    [--content-root DIRECTORY]
    [--map PREFIX=DIRECTORY]...
    [--frame NUMBER]
    [--uv-map NAME]
```

`inspect` détecte le format par sa signature, sans imposer d'extension, et écrit
un résumé JSON sur stdout. `convert` crée une sortie nouvelle, hors de la racine
de contenu ; son dossier parent doit exister. Les fichiers sources sont ouverts
en lecture seule. Le manifeste est écrit en dernier ; une erreur d'écriture peut
laisser une sortie incomplète sans manifeste, à supprimer ou à examiner avant de
relancer vers un autre dossier.

La racine de contenu vaut par défaut le dossier de l'entrée. Pour une scène,
choisir la racine du projet aide à éviter les collisions entre objets homonymes.
`--frame` accepte une frame fractionnaire et vaut par défaut `FirstFrame`.
`--uv-map` sélectionne explicitement une map TXUV native ; aucune map n'est
choisie automatiquement en fonction des matériaux.

Codes de sortie :

| Code | Signification |
|---|---|
| 0 | Lecture réussie ou export du sous-ensemble pris en charge |
| 1 | Erreur d'arguments, de lecture, de structure ou d'écriture ; diagnostic avec offset |
| 2 | Paquet produit avec éléments manquants, omis ou approximés ; consulter le manifeste |

Le code 0 ne garantit pas une restitution visuelle LightWave. Les matériaux MTL
restent une approximation scalaire, et le périmètre est inscrit dans chaque paquet.

## Lecture et conservation

| Entrée | Données interprétées dans ce jalon |
|---|---|
| LWOB | PNTS, SRFS, POLS avec surfaces signées et polygones de détail, PCHS, sous-ensemble SURF et références d'images |
| LWO2 | LAYR, blocs PNTS/POLS, TAGS/PTAG, VMAP/VMAD de toute dimension et de tout type, sous-ensemble SURF/CLIP |
| PST_ | Enveloppe de preset conservée et objet LWO2/LWOB imbriqué dans PDAT |
| LWSC 1/3 | Objets, nulls, identifiants des lumières/caméras/bones, parents, pivots, canaux de mouvement et clés, blocs de plugins |

Les bornes IFF, les tailles, les indices de points et les flottants de géométrie
sont contrôlés. Les indices invalides de maps/attributions sont conservés et
comptés. Les valeurs non finies de maps restent présentes dans le binaire et sont
signalées ; elles ne deviennent pas des UV exportés. La limite de lecture est de
512 Mio par fichier. Les tableaux sont limités à des indices 32 bits, avec bornes
de récursion pour les chunks imbriqués, les dossiers et la hiérarchie de scène.

Chaque entrée interprétée est copiée intégralement dans `source.bin`, avec son
SHA-256. Les champs non interprétés restent donc récupérables, y compris les
projections, plugins, enveloppes scalaires, réglages de rendu et chunks inconnus.
Les références d'images sont extraites mais les images ne sont pas encore
résolues, copiées ni décodées. Les dépendances absentes ou illisibles sont
signalées ; elles ne sont pas incorporées au paquet.

Les chaînes gardent leurs octets sous forme `raw_hex`. Le champ `text` utilise
UTF-8 si valide, sinon une hypothèse Latin-1 explicitement nommée. Une résolution
réussie par cette hypothèse ne prouve pas l'encodage d'origine. Sous Windows, les
chemins système passent par les API Unicode.

## Résolution des objets de scène

L'ordre est le suivant :

1. Mapping explicite `--map`, avec priorité au préfixe correspondant le plus long.
2. Chemin exact sous la racine de contenu, pour les références relatives.
3. Recherche par suffixe de composants, puis par nom seul, parmi les fichiers
   dont la signature est LWOB/LWO2 sous cette racine.

Par exemple, `--map "Y:meshes/=C:/archives/projet/meshes"` remplace un ancien
préfixe. Les lettres de lecteurs historiques ne déclenchent pas directement une
lecture du lecteur actuel. Les liens de répertoire ne sont pas parcourus lors
de la découverte. Un mapping explicite absent ne déclenche pas de repli implicite.

Une égalité entre plusieurs meilleurs candidats reste `ambiguous`, même si leurs
contenus sont identiques. Un candidat unique par suffixe ou nom seul est retenu
avec un statut indiquant cette heuristique. Les chemins candidats et le choix
sont enregistrés dans `scene.json`. Sous Windows, les comparaisons ignorent la
casse Unicode ; la branche POSIX ne replie que la casse ASCII.

`LoadObjectLayer n` est confronté à l'identifiant LAYR `n-1`, conformément aux
fichiers observés ; le nombre original est conservé. L'ordre physique des chunks
ne détermine pas le numéro de couche. Une couche absente ou un ID dupliqué rend
l'instance non résolue. Les objets résolus sont dédupliqués par chemin puis SHA-256.

## Paquet LWIR 0.1

```text
manifest.json
assets/<sha256>/
    source.bin
    object.json
    geometry.bin
    mesh.obj
    materials.mtl
scene/                    # entrée LWS seulement
    source.bin
    scene.json
    animation.bin
scene.obj                 # instantané si les transformations sont évaluables
scene.mtl
```

Les URI internes sont relatives au JSON qui les contient. Les chemins absolus
d'origine servent à la provenance. Ce schéma initial est versionné `0.1` et peut
encore évoluer ; il ne constitue pas encore un contrat d'archivage stabilisé.

`geometry.bin` utilise un ordre d'octets little-endian explicite, sans sérialiser
la disposition mémoire des structs C. Chaque vue JSON indique `offset`, `count`,
`stride`, `component_type` et `components`.

| Vue | Enregistrement |
|---|---|
| `positions` | 3 float32, 12 octets ; coordonnées LightWave inchangées |
| `indices` | 1 uint32, 4 octets ; indices globaux dans `positions` |
| `primitives` | 9 uint32, 36 octets ; champs nommés dans `primitive_fields` |
| Map `entries` | 2 uint32, 8 octets : point local, polygone local |
| Map `values` | `dimension` float32 par entrée, y compris dimension zéro |

Les neuf champs de primitive sont le premier indice, le nombre d'indices, le
FourCC du type, les flags, le bloc POLS, le matériau, le tag, le parent de détail
et les bits du numéro de surface LWOB signé. `4294967295` représente un indice
absent. Le dernier champ se relit comme un int32 pour retrouver le signe.
Les objets JSON conservent les blocs PNTS/POLS et leurs couches, les attributions
PTAG, les noms et les descriptions des maps. Une référence `layer` dans un bloc
est un indice dans le tableau `layers`, dont `id` conserve le numéro natif.

Les indices VMAP/VMAD sont locaux aux blocs natifs désignés. Une VMAP continue
utilise l'indice absent pour son polygone. Les VMAD gardent ainsi les valeurs
différentes aux coutures, sans fusionner les coins avec les sommets.

`animation.bin` contient des clés de 72 octets : huit float64 (`time`, `value`,
six paramètres), puis deux uint32 (`shape`, réservé à zéro). Les temps sont en
frames pour LWSC 1, en secondes pour LWSC 3 ; les rotations sont respectivement
en degrés et radians. Les pré/post-comportements, décalages temporels, nombres
de clés déclarés et modificateurs opaques restent dans les canaux JSON.

## OBJ et évaluation initiale

Les OBJ individuels conservent les n-gones, avec réflexion de Z pour passer au
repère droit Y-up choisi. L'ordre des coins est inversé selon le signe du
déterminant total. Les points et lignes simples sont exportés ; les points
non utilisés par une primitive exportée reçoivent un enregistrement `p`.

Les PTCH/PCHS sont exportés comme cages de contrôle, les CURV comme polylignes de
contrôle, avec compteurs d'approximation. Les bones, types de primitives inconnus,
polygones de détail et faces à indices répétés sont omis de l'OBJ et conservés
dans LWIR. Les anciens CRVS restent opaques. Les normales, le lissage et la
subdivision LightWave ne sont pas encore évalués.

Avec `--uv-map`, les VMAD prennent priorité sur les VMAP. Une face dont certains
coins n'ont pas de valeur valide sort sans indices UV ; aucun zéro n'est inventé
pour compléter la map. Un logiciel qui réimporte cet OBJ peut toutefois créer
ses propres UV par défaut. Les MTL contiennent couleur diffuse, spéculaire,
émission et transparence approximatives, sans liaison de texture.

Pour `scene.obj`, la matrice locale actuelle est
`T(position) × Ry(heading) × Rx(pitch) × Rz(bank) × S × T(-pivot)` ; elle est
composée avec les parents avant la conversion du repère. Les tests vérifient la
translation, la rotation de heading, le pivot, les parents et l'échelle négative.
Les pivots/parents de couches LWO2 non triviaux bloquent cet instantané tant que
leurs interactions avec Layout ne sont pas qualifiées.

L'évaluateur accepte les clés exactes, les interpolations linéaires et en
escalier, ainsi que les comportements reset, constant et repeat. Les paramètres
TCB/Hermite/Bézier sont conservés, mais l'échantillonnage à l'intérieur de ces
segments n'est pas implémenté. Les transformations pilotées par certains plugins,
des modificateurs de canaux, des pivots orientés, des bones ou l'IK bloquent
l'instantané. Les cycles et parents manquants sont également signalés.

Une scène avec des dépendances manquantes peut fournir un `scene.obj` partiel.
L'OBJ montre la géométrie de base des instances résolues : il n'applique pas les
morphs, déformations, masques de visibilité, dissolutions ni effets de rendu.
Les compteurs du manifeste décrivent les omissions et approximations sur
l'ensemble des OBJ produits, objets individuels et scène compris.

## Validation effectuée

- 19 tests de régression avec fichiers synthétiques : buffers, SHA-256, octets
  sources, encodages, padding, VX 24 bits, détails, couches, coutures VMAD, poids
  infinis, résolution des chemins, hiérarchie et mouvements, entrées tronquées,
  blocs de plugins et refus d'écrasement.
- Comparaison du lecteur C à l'inventaire Python indépendant : **1 143/1 143**
  fichiers, soit 915 objets, 226 scènes et deux presets. Concordance des compteurs
  et SHA-256, dont **1 162 552 points**, **1 450 682 primitives**, **1 137 maps**,
  **1 974 chargements d'objets** et **2 741 bones**.
- AddressSanitizer : aucun diagnostic d'accès mémoire sur les tests et le corpus.
  Lecture et SHA-256 vérifiés également sur les 64 LWOB et 11 LWS 1 de Freestyle.
- Réimport indépendant en Blender 4.2, en arrière-plan : Metropolis UV
  (633 sommets, 458 faces, comparaison de 1 752 coins UV, dont les coins sans
  liaison convertis en valeurs par défaut par Blender) et logo Freestyle
  (4 sommets, 1 face).
- Export de `circus/Mr_Lector_2.lws` : les deux instances demandant des couches
  absentes sont signalées ; les cages exportées sont comptées.

Le [rapport de validation](diagnostics/converter-validation.json) précise les
configurations et résultats. Ces contrôles établissent la récupération
structurelle et des propriétés d'export ciblées. Ils ne valident pas encore la
fidélité d'un rendu LightWave, l'ensemble de l'animation ni les autres plateformes.

Pour relancer la comparaison du corpus :

```powershell
python tests/check_corpus.py build/Release/lwconvert.exe --report build/corpus.json
```

Le test Blender est optionnel :

```powershell
python tests/check_obj_blender.py --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe" --obj output/lector/scene.obj --report build/obj-check.json
```

Il vérifie les comptes, arités et multisets d'UV par coin. Ce n'est pas encore
un test de correspondance UV par face ni un test de rendu.

Pour AddressSanitizer sous MSVC, configurer un autre dossier avec
`-DLWCONVERT_SANITIZE=ON`, compiler en `RelWithDebInfo` et ajouter le dossier du
runtime ASan du compilateur au `PATH` du processus de test. Sur les compilateurs
non MSVC, l'option demande AddressSanitizer et UndefinedBehaviorSanitizer ; cette
branche reste à qualifier.

## Références et prochaines étapes

Le dépôt `C:/works/projects/preservation-freestyle-by-syndrome-condense`, révision
`1ba8faabbab39cdcf163de53a64ea92e93f4b55d`, a servi à comparer la structure de ses
lecteurs `src/lwob.cpp` et `src/scene.cpp` et à sélectionner un exemple réel.
Ces lecteurs C++ comportent des conventions nXng, notamment le repère Y-down et
le traitement de clés distantes d'une frame comme des coupures. Ces conventions
ne sont pas importées dans le convertisseur générique. Leur code GPL n'a pas été
copié dans cette implémentation C ; les nouveaux fichiers suivent la licence du dépôt.

Les descriptions des champs natifs proviennent du SDK NewTek archivé :
[objets LWO2](https://documentation.help/LightWave/lwo2.html) et
[scènes LWSC 3](https://documentation.help/LightWave/lwsc.html). Les variantes du
corpus complètent cette documentation : notamment les numéros de couches,
les presets et les enveloppes dont le nombre de clés déclaré est incohérent.

La suite proposée est d'évaluer les courbes d'animation et les projections de
textures, puis d'ajouter le writer glTF 2.0 et l'adaptateur Python pour `.blend`.
Ce dernier sera lancé par un processus Blender en arrière-plan ; aucun addon
personnalisé n'est nécessaire. Les trois sorties doivent partager LWIR et les
mêmes dérivations qualifiées de géométrie, de matériaux et d'animation.
