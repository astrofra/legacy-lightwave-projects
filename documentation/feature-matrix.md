# Matrice des fonctionnalités — lwconvert 0.19.2

État vérifié le **14 septembre 2026**, à partir du code, des tests et du
[batch du 13 septembre à 22:11](batch-20260913-221149-priorities.md), complété par
la [QA des projections de la version 0.16.0](texture-projections.md) et la
[QA des clip maps Butterfly et Dora, versions 0.17.0–0.18.0](clip-maps.md).
La [QA Snow Tanks](snow-tanks-fog-qa.md) précise les limites de transparence
additive et la [QA morph Snow Tanks/Red Line](morph-targets.md) couvre le
dissolve par instance et le morphing LWS.
Cette page décrit le comportement actuel ; les autres rapports de QA peuvent
décrire une version antérieure. Les versions ci-dessous sont celles des
**fichiers**, pas celles du logiciel LightWave qui les a enregistrés.

Le périmètre par défaut est le **convertisseur C autonome** et son batch,
avec `--bake-ik auto`, `--skin-profile auto` et `--gltf-rigs skins`.
Le recours facultatif à LightWave est décrit séparément en fin de page.

## Lecture de la matrice

| Symbole | Sens |
| --- | --- |
| **S** | Pris en charge dans le périmètre indiqué par la ligne. |
| **P** | Partiel, soumis à conditions, ou interprétation approximative. |
| **B** | Octets préservés, sans interprétation complète de leur contenu. |
| **N** | Non pris en charge à cette étape. |
| **—** | Hors du profil de ce format ou sans objet pour cette colonne. |

Les colonnes LWOB/LWO2/LWSC indiquent ce que le **lecteur actuel interprète**,
pas une liste exhaustive de tout ce que les spécifications natives autorisent.
Les colonnes IR, OBJ et glTF indiquent ensuite ce qui est réellement conservé
ou traduit. Une fonctionnalité peut donc être **S dans l'IR et N en glTF**.

Une entrée acceptée conserve son `source.bin` identique et son SHA-256.
Les chunks/paramètres opaques ne sont pas perdus pour autant, mais leur
présence dans ces octets n'implique ni champ IR sémantique ni export utilisable.
Un format refusé n'obtient pas automatiquement un package IR : son original
reste dans `content/`.

## Versions de fichiers

| Format/signature | Lecture | Détails et limites |
| --- | --- | --- |
| `FORM LWOB` | **P** | Géométrie, surfaces et textures historiques ; plusieurs types/chunks restent opaques. |
| `FORM LWO2` | **P** | Calques, polygones, tags, maps, surfaces et images ; aucun engagement de couverture intégrale de LWO2. |
| `FORM PST_` | **P** | Extraction de l'objet/surface LWOB ou LWO2 embarqué dans `PDAT` ; ce n'est pas une scène. |
| `FORM LWLO`, `FORM LWO3`, autres FORM 3D | **N** | Non reconnus par le batch comme objets pris en charge ; refus en conversion directe. |
| `LWSC 1` | **P** | Anciennes listes de clés ; temps en frames, rotations de mouvement en degrés ; caméra implicite reconnue. |
| `LWSC 3` | **P** | Canaux/enveloppes ; temps en secondes, rotations de mouvement en radians. |
| `LWSC 5` | **P** | Profil `lwsc5-partial-0.1`, IDs explicites, extraction des données et exports conditionnels ; statut toujours `partial`. Baking IK autonome non disponible. |
| `LWSC 2`, `LWSC 4`, autres versions | **N** | Refus explicite du lecteur ; LWSC 4 n'est pas assimilé à LWSC 5. |
| Extension absente ou atypique | **S** | La signature détermine le format, par exemple les anciennes scènes Amiga sans `.lws`. |

`LWSC 3` ne permet pas de distinguer à lui seul toutes les sémantiques de
skinning des différentes générations du logiciel. Le choix automatique actuel
privilégie le profil le plus ancien : `lightwave6` pour LWSC 1/3 et
`lightwave96` pour LWSC 5. C'est une **politique de conversion**, pas une
détection certaine de la version du logiciel. Aucun preset n'est sauvegardé.

## Modèle et géométrie

| Fonctionnalité | LWOB | LWO2 | IR | OBJ | glTF |
| --- | --- | --- | --- | --- | --- |
| Positions, indices, identité des points et contours natifs | S | S | S, valeurs et indices originaux | P, géométrie dérivée | P, géométrie dérivée et provenance par coin |
| Triangles et n-gones simples/concaves | S | S | S | S, triangulation | S, triangulation |
| Trous reliés par des arêtes parcourues en sens inverse | S | S | S | P, contours valides | P, contours valides |
| Excursions retracées `A → B → A`, y compris imbriquées | S | S | S, contour intact | S depuis 0.15.1 | S depuis 0.15.1 |
| Faces non planes | S | S | S | P, projection et diagnostic | P, projection et diagnostic |
| Faces croisées/dégénérées hors profil | S | S | S | N, omises et signalées | N, omises et signalées |
| Points et lignes | S | S | S | S, `v`/`p`/`l` selon primitive | S, points/lignes, y compris points non utilisés par les faces exportées |
| Polygones de détail LWOB et surfaces signées associées | S | — | S | N | N |
| Calques `LAYR`, noms, flags et sélection `LoadObjectLayer` | — | S | S | P, sélection et géométrie | P, sélection et géométrie |
| Pivot et parent d'un **calque LWO2** | — | S | S | P, certains snapshots bloqués | P, certains snapshots/skins bloqués |
| MetaNURBS/SubPatch `PCHS`/`PTCH` | S (`PCHS`) | S (`PTCH`) | S, cage et type | P, cage seulement | P, cage triangulée seulement |
| Autres types de polygones, dont `SUBD`/`BONE` | — | P, enregistrement générique | S, type et indices ; sémantique non évaluée | N | N |
| Courbes `CURV` | — | S | S | P, polyligne de contrôle | P, polyligne de contrôle |
| Maps génériques `VMAP`/`VMAD` | — | S | S, type/nom/dimension/valeurs | P, seulement maps exploitées | P, seulement maps exploitées |
| Couleurs de sommets `RGB `/`RGBA` | — | S, map générique | S | N | N, pas de `COLOR_0` |
| Morphs `MORF`/`SPOT` | — | S | S, valeurs natives et noms | N, pas de déformation morph | S depuis 0.19.0, cibles `POSITION` et `NORMAL`, valeurs absentes = déplacement nul |

**La subdivision n'est volontairement pas figée dans les exports.** Les
paramètres LWS `SubPatchLevel` et `SubdivisionOrder` restent également dans
les paramètres natifs de l'IR. Le futur backend Blender devra permettre de
réutiliser ces données. Un pivot de calque LWO2 n'est pas le `PivotRotation`
d'un item LWS : le support du second ne résout pas celui du premier.

Références : [lecteur LWO](../src/lwo.c), [triangulation](../src/triangulate.c),
[OBJ](../src/obj.c), [glTF](../src/gltf.c), [QA Cyber-bot](cyber-bot-ngons-20260913-qa.md).

## Surface, normales et lissage

| Fonctionnalité | LWOB | LWO2 | IR | OBJ/MTL | glTF |
| --- | --- | --- | --- | --- | --- |
| Noms et attributions de surfaces `SRFS`/`SURF`/`PTAG` | S | S | S | S | S |
| Couleur, diffuse, luminosité, spécularité, transparence scalaires | S | S | S | P, approximation MTL | P, approximation de matériau PBR |
| Transparence additive de surface (`ADTR` LWO2, flags LWOB) | P, flags bruts | B, sous-bloc non interprété | P/B, flags ou octets source ; pas de champ additif structuré | N | N, export potentiellement opaque ; voir Snow Tanks |
| Glossiness, réfraction et shader LightWave complet | B | B/P selon bloc | B/P | N, pas de restitution complète | N, pas de restitution complète |
| Face avant/double face, `FLAG`/`SIDE` | S | S | S | N, pas de liaison MTL explicite | P, dont `doubleSided` ; variantes signalées |
| Angle de lissage `SMAN` et activation du lissage LWOB | S | S | S, valeur brute et interprétation effective | S, normales `vn` | S, attribut `NORMAL` et métadonnées |
| Héritage d'une surface LWO2 | — | P | S, nom de la source | P, héritage du lissage seulement | P, héritage du lissage seulement |
| Groupes de lissage `PTAG/SMGP` | — | S | S | S, frontières de normales et `s` | S, frontières portées par `NORMAL` |
| Normales explicites `VMAP/NORM` et `VMAD/NORM` | — | S | S | P, map unique non ambiguë | P, map unique non ambiguë |
| Coutures UV/normales et coins discontinus | — | S | S | S, indices par coin | S, duplication des sommets de rendu |
| Shaders/procédures de surface non reconnus | B | P, en-têtes et données opaques | B/P | N | N |

Les normales explicites sont normalisées **dans les dérivés** ; leur valeur
native reste en IR. La moyenne de lissage suit les polygones sources, pas
les triangles produits. Les cas `SMGP` et `NORM` ont des tests synthétiques.
L'audit courant ne relève aucune map `NORM` ; il n'inventorie pas les
attributions `SMGP`. Il ne remplace donc pas leur qualification spécifique.

Le matériau glTF reste une approximation ; préserver `SPEC` ne garantit pas
la restitution du BRDF LightWave. Les images spéculaires utilisent le profil
`KHR_materials_specular`, détaillé dans la section texture.

Références : [normales et mesures](normals-qa.md), [surfaces](../src/lwo.c),
[calcul des normales](../src/normals.c), [export des matériaux](../src/gltf.c).

## Texture : références et application

| Fonctionnalité | LWOB | LWO2 | IR | OBJ/MTL | glTF |
| --- | --- | --- | --- | --- | --- |
| Références `TIMG`/`RIMG` ou `CLIP/STIL` | S | S | S, chemin, rôle, résolution, hash et original trouvé | P, selon image et mapping | P, selon image et mapping |
| Recherche de chemin et extension d'image alternative | S | S | S, choix/candidats/ambiguïtés | S, si liaison compatible | S, si liaison compatible |
| Noms de textures lisibles avec préfixe et gestion des collisions | S | S | S, provenance | S | S |
| Projection objet planaire/sphérique, axes X/Y/Z et centre | S | S | S | S, coordonnées par coin dans le profil statique | S, coordonnées par coin dans le profil statique |
| Taille planaire, rotation HPB LWO2, répétition sphérique | S, sans rotation native | S | S, paramètres natifs distincts des dérivés | S, paramètres statiques | S, paramètres statiques |
| Coutures et pôles sphériques | S | S | S, topologie native intacte | P, longitude par polygone | P, longitude par polygone |
| Hors image : reset, repeat, mirror, edge | S | S | S, wrap natif et domaine dérivé | P, atlas borné pour les modes non repeat | P, même atlas ; filtrage approché |
| Projection cylindrique/cubique/autres LWOB | P | — | P, paramètres et octets | N | N |
| Image UV LWO2 `BLOK/IMAP`, `IMAG`, `PROJ 5`, `TXUV` | — | S | S | P, profil borné | P, profil borné |
| Autres projections d'images LWO2 : cylindrique, cubique, frontale | — | S/P | S/P, paramètres et octets | N | N |
| UV discontinus `VMAD/TXUV` | — | S | S | S, `vt` par coin | S, `TEXCOORD_0` |
| Multiples couches, blend/opacity, transformations animées | P | P | P, paramètres connus et octets | P très limité | P très limité |
| Procédurales et gradients `PROC`/`GRAD` | P | P | P, en-têtes ; payload partiellement opaque | N | N |
| Couleur et diffuse texturées | P | P | S/P | P, `map_Kd` composé | P, `baseColorTexture` |
| Luminosité texturée | P | P | S/P | P, `map_Ke` | P, `emissiveTexture` |
| Transparence de surface texturée | P | P | S/P | P, opacité dans `map_d` | P, alpha et `BLEND` |
| Spéculaire texturée | P | P | S/P | P, `map_Ks` | P, `KHR_materials_specular` |
| Bump de hauteur | P | P | S/P | P, `bump`/`-bm` | N, pas encore de conversion hauteur → normales |
| NormalShader MicroWave, image et espace objet | — | P | P, shader et dérivé documentés | N, PNG disponible mais pas de liaison MTL | P, PNG tangent + `normalTexture` + `TANGENT` |
| Normal map monde | — | P | P | N, pas de liaison MTL | P, matrice du bake connue requise |
| Normal map déjà tangentielle | — | P | P | N, pas de liaison MTL | P, convention explicitement affirmée |
| Réflexion environnement et projections monde générales | P | P | P, paramètres/octets/image | N, éclairage non reproduit | N, éclairage non reproduit |
| Séquences d'images et animation de texture | B/P | B/P | B/P | N | N |

Les contraintes du profil image LWO2 sont : une couche image active par canal,
blend normal à 100 %, paramètres statiques et espace objet. La voie UV exige
encore une transformation de texture identité et des UV complets ; la voie
projetée interprète centre, taille planaire et rotation. Plusieurs canaux doivent partager un mapping
compatible pour les cartes composées. Un bloc désactivé est conservé sans être
appliqué : ce diagnostic ne demande pas de « correction ».

La taille sphérique est conservée mais n'affecte pas le calcul, conformément
aux mesures natives. Les modes non repeat produisent un atlas et un remappage
UV ; ils sont refusés si le raster nécessaire dépasse les limites ci-dessous.
Les enveloppes variables, le falloff, les références de scène et les coordonnées monde
ne sont pas évalués par ce profil. Sur une face sphérique peu dense, les UV
restent interpolés entre les coins ; aucune subdivision n'est imposée.

Depuis la v0.19.2, les couches image acceptent les enveloppes prouvées constantes
aux valeurs natives : clés égales, spans `LINE`/`STEP` et comportements hors
plage compatibles. Les clés originales restent préservées. Voir le
[profil des enveloppes constantes](textures.md#constant-texture-envelopes).

Depuis la v0.19.1, la recherche d'images remonte au **parent du dossier du
LWO/LWS propriétaire**, puis explore tous ses descendants, y compris les
dossiers frères, quel que soit leur nom. Elle ne remonte pas davantage et reste
indépendante du content root. Les chemins relatifs au propriétaire restent
prioritaires, y compris ceux qui contiennent `..`.
La préférence d'extension est PSD, TGA, PNG, JPEG, JPG, GIF, TIFF/TIF, puis les
autres formats reconnus. Un nom exact passe avant une extension alternative ;
une ambiguïté restante est signalée. Ce classement ne garantit pas le décodage
de toutes les variantes de chaque format.

L'inférence du content root pour les **objets d'une scène** est distincte :
elle exploite l'ensemble des références et peut rechercher dans le parent
et ses sous-dossiers. Les règles explicites restent prioritaires. Ne pas
confondre ces deux périmètres de recherche.

### Décodage des images, indépendant de la version LWO/LWS

| Image source | Décodage | Conservation et limites |
| --- | --- | --- |
| PNG, JPEG, TGA, BMP | P | Variantes acceptées par stb ; original trouvé archivé, pixels utilisables pour les dérivés. |
| PSD | P | Composite RGB via stb ; gris mono-canal 8/16 bits raw/PackBits via lecteur C. Pas de restitution des calques Photoshop. |
| GIF | P | Première image seulement, pas l'animation. |
| IFF `FORM ILBM` | P | Palette 1–8 plans, EHB, RGB24/RGBA32, masques ; brut/ByteRun1 ; PNG dérivé sans compression destructive. |
| HAM, autres formes IFF | N | Original trouvé conservé, diagnostic de décodage. |
| TIFF/TIF | N | Extension recherchée et original trouvé conservé ; aucun décodeur TIFF intégré actuellement. |
| Formats/variantes non reconnus, données tronquées | N | Pas de pixels inventés ; raison dans `decode_issue`. |

Limites des rasters : 16 384 pixels par axe et 16 777 216 pixels au total.
Le pipeline produit des dérivés RGBA8 ; ce n'est pas une conservation de toute
la dynamique/couleur du fichier source, lequel reste archivé séparément.

Références : [profil texture](textures.md), [NormalShader/MikkTSpace](normal-maps.md),
[résolveur](../src/images.c), [qualification des mappings](../src/textures.c),
[décodage](../src/raster.c), [noms des textures](texture-naming-audit.md).

## Scène et animation

| Fonctionnalité | LWSC 1 | LWSC 3 | LWSC 5 | IR | OBJ | glTF autonome |
| --- | --- | --- | --- | --- | --- | --- |
| Items, noms, objets/calques, parents | S | S | S | S | P, snapshot assemblé | P, hiérarchie et instances |
| IDs explicites modernes, non séquentiels | — | — | S | S | P, via hiérarchie | P, via hiérarchie |
| Recherche/inférence du content root et règles `--map` | S | S | S | S, références et candidats | P, objets résolus seulement | P, objets résolus seulement |
| Clés et canaux de mouvement d'origine | S | S | S | S, clés et paramètres natifs | P, échantillon rigide à `--frame` | P, canaux évaluables |
| Translation, rotation HPB, échelle, parentage | S | S | S | S | P, snapshot | P, pistes TRS échantillonnées |
| Pivot déplacé et `PivotRotation` d'item | S | S | S | S | P, transformée qualifiée | P, depuis 0.15.0 ; règles du bake distinctes |
| TCB, linéaire, constant/stepped, comportements reset/constant/repeat | P | P | P | S pour les clés natives | P, évaluation | P, échantillonnage, pas courbes natives |
| Autres interpolations/behaviors et modificateurs | P | P | P | S/B selon donnée | N si nécessaires au snapshot | N si nécessaires à l'animation |
| Bones : parents, repos, `BoneRestDirection`, pivot orienté | P | S | P | S | N pour le squelette/déformation | P, rig/rest/skin selon compatibilité |
| Poids explicites normalisés `WGHT` + associations LWS | P | S | P | S | N | P, skin, tous les jeux d'influences nécessaires |
| Influences procédurales et skin hybride | P | S | P | S natif + dérivé distinct | N | P, approximation de poids autonome |
| Poids négatifs/non normalisés, `UseBonesFrom`, hiérarchies hors profil | P | P | P | S/B | N | N pour les variantes refusées, diagnostic |
| Joint compensation / muscle flexing | P | S | P | S | N | N pour la correction de volume ; omission ou refus du rig selon profil |
| FK squelettique échantillonnée par le baker C | P | P | N | S, original + bake distinct | N pour la déformation skinnée | P, si le rig et les contrôleurs sont compatibles |
| IK autonome : axes HPB, limites, objectifs, ancrage | P | P | N | S, contraintes + bake distinct | N, pas de snapshot IK par cette voie | P, profil `autonomous-hpb-ik-0.2` |
| Orientation d'objectif / raideur / cible inaccessible | P | P | N | S, données et diagnostics | N | P, approximation à qualifier par scène |
| Autres contrôleurs de position/rotation, spline/path | P | P | P | S/B, paramètres préservés | N si requis | N si requis |
| `LW_Follower`, cas de miroir de bank qualifié | P | P | P | S/B | P, profil spécifique | P, animation ordinaire ; pas d'acceptation générale par le baker IK |
| Expressions, MotionMixer, plugins de mouvement/canal généraux | B | B | B | B, blocs et références source | N si requis | N si requis ; peuvent bloquer le bake |
| `MorphTarget` / `LW_MorphMixer` / animation des `MORF` | P | S/P | P | S, cible externe, formes, valeurs, enveloppes, paramètres et source brute | N | P depuis 0.19.0 : cible objet compatible, formes nommées et pistes `weights` échantillonnées |
| Clip maps binaires par instance | P | P | P | S/P, arbre natif, provenance et évaluation distincte | P, image `map_d` seuillée | P, images statiques compatibles, dont `ClipMap` plan LWSC1 ; `MASK` à 0,5 |
| `ObjectDissolve` et son enveloppe | B | B | B | S/P, valeur statique structurée ; enveloppe brute | N | P, une valeur statique ≥ 1 masque l'instance ; valeurs partielles et enveloppes non traduites |
| Caméras, lumières, mouvements associés | P | P | P | P, items et canaux ; paramètres complets dans la source | N pour caméra/éclairage natifs | N pour caméra/éclairage natifs |
| Filtres d'image, profondeur de champ, effets de rendu, cheveux | B | B | B | B, plugins/énoncés source | N | N |
| Énoncés inconnus indexés par plage d'octets | B | B | S | LWSC 5 : index consultable ; autres : source brute | N | N |

Les statuts de cette table n'affirment pas que chaque champ a été observé dans
chaque version. LWSC 5 a huit scènes de corpus et un profil partiel ; les mêmes
fonctions internes de lecture ne suffisent pas à qualifier toutes ses variantes.

Les pistes glTF sont échantillonnées à la fréquence de la scène. Le baker IK
est borné à 4 096 nœuds, 1 001 poses, 64 MiB de poses, 96 axes libres et
32 cibles **par groupe**, avec 120 itérations par groupe. Son résultat est
approximatif. Les clés/contraintes natives restent séparées de
`baked-animation.json/bin` et des poids procéduraux dérivés.

Depuis la v0.19.0, les morphs LWO2 `MORF` (déplacement relatif) et `SPOT`
(position absolue) deviennent des cibles glTF par point source, puis par coin
de rendu après triangulation et coutures UV. `LW_MorphMixer` et `MorphAmount`
sont échantillonnés à la cadence de la scène. Les cibles objet exigent une
topologie et un ordre des points identiques. `MorphSurfaces`, `MTSEMorphing`,
les maps discontinues et les modificateurs d'enveloppe restent signalés.
Le glTF applique ces morphs avant le skinning, conformément à son modèle.
Voir [le profil et la QA morph](morph-targets.md).

Un seul skin compatible peut être publié dans le glTF principal de la scène.
Les scènes à plusieurs skins gardent des dérivés par rig ; une animation
présente dans un rig n'est pas nécessairement assemblée dans le fichier de
scène principal. Les copies de squelettes sans skin sont désactivées par
défaut (`--gltf-rigs all` permet de les demander).

Les clip maps sont conservées avec leur sémantique de découpe binaire. En
v0.17.0, un `TextureBlock` avec une image statique plane/sphérique alignée sur
le matériau produit `map_d` pour OBJ et l'alpha de base color avec `MASK` pour
glTF. La v0.18.0 ajoute le format legacy `ClipMap Planar Image Map / Texture...`
et les différences de taille/centre/wrapping entre plans de même axe sans
rotation, si l'atlas couleur Reset/Edge couvre toute la géométrie. Les
procédurales, les piles, les autres projections incompatibles, les références
monde/objet et les enveloppes restent préservées sans évaluation.
La transparence ordinaire reste en `BLEND`.
Voir le [périmètre exact et la QA Butterfly/Dora](clip-maps.md).

Références : [lecteur/évaluateur LWS](../src/lws.c), [skinning](../src/skin.c),
[IK](../src/ik.c), [poids procéduraux](procedural-skinning.md),
[pivots orientés](oriented-pivots.md), [profil LWSC 5](lwsc5-partial-support.md).

## LightWave facultatif et cible Blender

| Voie | État | Dépendances et périmètre |
| --- | --- | --- |
| Conversion C par défaut | S/P selon les tables | Aucun LightWave/Blender requis à l'exécution ; SDK utilisé seulement comme documentation/oracle de développement. |
| Évaluation native facultative | P | Wrapper Python + LightWave installé + plugin de capture ; LWSC 1/3, profils LW6/9.6 bornés. Peut fournir des poses évaluées, animations de skins ou morphs échantillonnés. |
| Morphs issus de capture native | P | Déformation échantillonnée de la cage, distincte d'un export autonome des morphs nommés et de leurs enveloppes. |
| Lecture du glTF final | S selon le lecteur | Aucun LightWave requis ; les QA Blender/Khronos mesurent des propriétés différentes. |
| Écriture directe d'un fichier `.blend` | N | Backend à implémenter. Importer le glTF dans Blender n'est pas ce backend. |
| Choix Blender : poses bakées ou IK native éditable | Prévu | Exigence retenue, avec différences de solveur assumées pour l'IK native ; pas encore une option opérationnelle. |

Références : [passerelle native](../tools/export_lightwave_animation.py),
[animation Smilla LW6](smilla-lightwave6-animation.md),
[roadmap Blender/IK](lightwave-ik-oracle-feasibility.md).

## Actualiser cette page

À chaque évolution, mettre à jour la ligne concernée, sa version et son test
ou sa QA de référence. La matrice ne se déduit pas seulement du nombre de
`partial` : ce statut peut décrire une approximation voulue, un choix de
préservation, une ressource absente ou une fonction manquante.

L'[audit reproductible du batch](batch-20260913-221149-priorities.md) fournit
les volumes et cas d'essai pour les prochaines priorités. Il mesure la
présence des fonctionnalités et les diagnostics, pas leur fidélité visuelle
sur tout le corpus.
