# Audit du batch 20260913-221149 : priorités de couverture

Audit effectué le **14 septembre 2026** sur les logs, manifests et IR de
`output/batch-20260913-221149/packages`, puis sur les sources archivées pour
la triangulation. Version constatée dans les 1 799 manifests : **lwconvert 0.15.1**.
La [matrice des fonctionnalités](feature-matrix.md) décrit le support actuel
par catégorie, format source et cible. Les propositions ci-dessous décrivent
les priorités au moment de cet audit de la version 0.15.1.

Mise à jour : les projections planaires et sphériques de la priorité 1 sont
désormais implémentées en espace objet statique dans la
[version 0.16.0](texture-projections.md), avec 97 blocs nouvellement exportables
sur le corpus ciblé. Les chiffres ci-dessous restent ceux du batch initial.

## Périmètre et méthode

| Mesure | Résultat |
| --- | ---: |
| Conversions reconnues | 1 799 |
| Statut `converted` | 232 |
| Statut `partial` | 1 567 |
| Échecs de conversion signalés par le batch | 0 |
| Entrées `skipped`, hors conversions reconnues | 2 984 |
| Logs lus | 1 799 |
| Scènes | 428 : 157 LWSC 1, 263 LWSC 3, 8 LWSC 5 |
| Scènes avec un glTF principal déclaré dans le manifest | 399 |
| Objets natifs distincts par SHA-256 | 1 331 : 821 LWOB, 510 LWO2 |

Les objets ci-dessus incluent ceux chargés par les scènes et ceux extraits
des presets ; ce ne sont pas seulement les fichiers d'entrée autonomes.
Les 29 scènes sans glTF principal peuvent avoir des dérivés d'objets ou de
rigs. Les entrées ignorées comprennent des documents, archives, ressources et
formats non convertis ; l'absence d'échec ne signifie pas une couverture totale.

Le batch emploie les valeurs par défaut `bake_ik=auto`, `skin_profile=auto`,
`gltf_rigs=skins`, `normal_space=auto`, sans runtime LightWave configuré.
Le statut `partial` ne mesure pas la gravité : il peut signaler un matériau
approximatif, une dépendance introuvable ou une fonction non évaluée.

Les compteurs des manifests répètent les objets partagés entre scènes.
L'audit déduplique les objets par hash natif, et les blocs de textures par
hash de l'objet, index de matériau et index de bloc. Une même référence
d'image peut avoir des résultats de résolution différents selon son package.
Les diagnostics peuvent se recouvrir et ne donner que le **premier obstacle**.
Il ne faut additionner ni les catégories de causes ni les totaux OBJ et glTF.

## 1. Textures : étendre les projections d'images LWO2

**257 blocs de textures sur 110 objets LWO2 distincts** rencontrent d'abord
`LWO2 projection not supported; UV image maps only`. Le lecteur conserve
des paramètres que l'exporteur ne sait actuellement exploiter qu'en UV.
C'est une extension de couverture concrète, avec une infrastructure de
projections planaires/sphériques déjà présente pour LWOB.

Cas de départ :

- `animation-goeland/scene4/Objects/centre_control.lwo` ;
- `animation-goeland/scene4/Objects/clavier02.lwo` ;
- `butterfly-tank/01.lwo`.

**Premier jalon proposé :** qualifier les projections d'images planaires et
sphériques LWO2 en espace objet, avec paramètres statiques, puis produire les
UV par coin et les maps OBJ/glTF. Traiter ensuite cylindrique/cubique et les
transformations supplémentaires dans des étapes distinctes. Garder les UV
et paramètres natifs dans l'IR, sans les remplacer par les données calculées.

**Critère de validation :** les faces ciblées portent la bonne image, à la
bonne orientation et échelle, coutures comprises ; comparaison avec LightWave
sur quelques cas et absence de régression sur les images UV LWO2 existantes.
Les 110 objets sont un périmètre de diagnostic, pas une promesse de 110
conversions corrigées : un autre obstacle peut apparaître après la projection.

Autres volumes utiles pour organiser les étapes suivantes :

| Premier obstacle de texture | Blocs distincts | Objets distincts |
| --- | ---: | ---: |
| Image non résolue ou non décodable | 400 | 161 |
| Champ `PNAM` non pris en charge, principalement gradients | 494 | 119 |
| Champ `VALU` non pris en charge, principalement procédurales | 345 | 57 |
| Type LWO2 procédural/gradient/shader non évalué | 221 | 77 |

Ignorer `PNAM` ou `VALU` ne rendrait pas ces textures fonctionnelles : il
faudrait interpréter la procédure ou le gradient. Ce chantier est plus large
que la projection d'une image existante. Les **60 blocs désactivés sur 41
objets** ne doivent pas, eux, être appliqués à l'export.

Références : [qualification des textures](../src/textures.c),
[profil et QA](textures.md), [diagnostics détaillés](diagnostics/batch-20260913-221149-features.json).

## 2. Animation : exporter les morphs nommés et MorphMixer

**76 objets LWO2 distincts contiennent des maps `MORF`**, et **65 scènes
déclarent `DisplacementHandler / LW_MorphMixer`**. Le C préserve les maps et
les blocs natifs, mais ne traduit pas leurs cibles nommées et enveloppes en
animation glTF. La présence du plugin ne prouve pas que les 65 scènes ont
toutes des morphs actifs ou animés : il faut d'abord qualifier leurs clés.

Cas de départ : `HellBoy/hb_01.lws`, `HellBoy/hb_pan.lws` et
`aliens@newtek/01.lws`, complétés par les cas du
[corpus Redline](redline-animation-corpus.md).

**Premier jalon proposé :** relier les canaux MorphMixer aux maps nommées,
conserver les enveloppes originales et exporter les deltas de cibles glTF
avec leurs pistes `weights`. Reporter les deltas sur les sommets de rendu
dédoublés aux coutures ; qualifier l'ordre morph puis skin et le cas des
maps absolues `SPOT` séparément. La cage reste sans subdivision figée.

**Critère de validation :** plusieurs poses de référence, morph isolé puis
morph combiné au skinning, concordent avec LightWave ; le glTF s'anime dans
un lecteur indépendant et les clés natives restent accessibles en IR.
La capture native de géométrie animée déjà disponible constitue une autre
voie ; elle ne remplace pas cet export autonome de cibles nommées.

L'IK autonome est déjà active : **27 scènes obtiennent un bake approximatif**,
contre **26 refus explicites**. Parmi ces refus, huit concernent des plugins
de mouvement/canal sur les variantes Smila. Ils demandent une qualification
distincte ; ajouter MorphMixer ne suffira pas à les débloquer.

Les **212 scènes / 2 968 déclarations** de plugins non évalués ne représentent
pas 212 rigs cassés : ces déclarations incluent des filtres de rendu, outils
d'interface et effets sans incidence nécessaire sur les poses. Le profil C
actuel ne bake pas non plus l'IK des scènes LWSC 5.

Références : [lecteur LWS](../src/lws.c), [baker C](../src/ik.c),
[skinning](procedural-skinning.md), [profil LWSC 5](lwsc5-partial-support.md).

## 3. Géométrie : cibler les rejets de triangulation récupérables

Les manifests signalent **125 373 occurrences** de faces rejetées pour glTF
dans **465 conversions**. Ce total répète les assets. Pour le qualifier,
le probe C a relu chaque `source.bin` distinct après vérification de son hash,
sans reconversion ni modification des packages.

Sur **2 148 509 polygones `FACE`** à au moins trois coins, **39 619 sont
rejetés**, répartis sur **210 objets distincts** :

| Diagnostic du triangulateur | Faces sources | Objets concernés |
| --- | ---: | ---: |
| Aire projetée nulle ou contour dont les contributions s'annulent | 34 490 | 92 |
| Arêtes projetées croisées, superposées ou en contact | 4 453 | 133 |
| Dernier triangle dégénéré | 643 | 30 |
| Aucun triangle valide trouvé par découpage d'oreilles | 33 | 5 |

Les comptes d'objets par cause se recouvrent. Ce probe exclut les cages
`PCHS`/`PTCH` et les polygones de détail LWOB ; ses nombres ne sont donc pas
directement comparables aux totaux de toutes les primitives exportées.

Deux versions de `baie_statique.lwo`, dans `animation-goeland/scene3/Objects`
et `animation-goeland/scene1/Objects`, concentrent à elles seules **31 500
rejets pour aire projetée nulle**. Il serait trompeur de transformer ce
nombre en estimation de défauts visibles ou de faces à remplir.

**Premier jalon proposé :** isoler les **676 faces** des deux dernières
catégories et comparer leur contour natif avec la triangulation attendue.
Cas utiles : `film-kafka-3d-student-works/src/scarabplotch.lwo`,
`scarabfini.lwo`, `scarbody.lwo` dans le même dossier, et
`1st-year-student-amiga-project/Bunk/Perso/Robot`.
Ce sont des candidats à la QA, pas 676 bugs déjà démontrés.

**Critère de validation :** récupérer les contours géométriquement valides,
sans triangle hors contour, sans boucher les trous, avec conservation du
winding, des UV et de la provenance des coins. Les contours réellement
invalides restent diagnostiqués. Les cas non plans dont la projection
s'annule nécessitent aussi une qualification avant toute réparation.

Le correctif Cyber-bot des excursions `A → B → A` est déjà dans ce batch
0.15.1 ; il ne constitue pas un travail encore à faire.

Références : [triangulateur](../src/triangulate.c),
[probe](../tests/triangulation_probe.c), [QA Cyber-bot](cyber-bot-ngons-20260913-qa.md).

## Dépendances : un axe de QA transversal

**77 scènes ont 264 instances d'objets non résolues** :

| Résolution | Instances | Scènes |
| --- | ---: | ---: |
| Objet introuvable | 200 | 43 |
| Plusieurs candidats | 59 | 31 |
| Objet trouvé, calque demandé absent | 5 | 3 |

Exemples : `ActualStars` dans les scènes Aminet, `tank/01.lwo` dans
`butterfly-tank`, les calques de `circus/Mr_Lector_2.lws`.
Le mot « introuvable » signifie absent du périmètre de résolution, pas
nécessairement absent de tout le disque. Pour chaque cas, distinguer une
racine mal retrouvée, une ambiguïté et une dépendance effectivement manquante.
Les choix explicites de mapping permettent déjà de qualifier ces cas.

Côté images, l'inventaire dédupliqué relève **509 résultats `missing` et
19 `ambiguous`** pour les références natives des objets et scènes ; il ne
s'agit pas de 528 fichiers images distincts. Une référence peut apparaître
dans plusieurs états selon le package. Garder le périmètre de recherche
autorisé — dossier du propriétaire et descendants — et examiner les candidats
avant d'étendre une règle. L'inférence de racine pour les objets LWS est
distincte et autorise déjà la recherche depuis le parent.

## Reproduire et actualiser l'audit

Depuis la racine du dépôt, avec le probe compilé de la même version :

```powershell
python -u -X utf8 documentation/diagnostics/analyze_batch_features.py output/batch-20260913-221149 --report documentation/diagnostics/batch-20260913-221149-features.json --triangulation-probe build/Release/triangulation_probe.exe
```

Sans `--triangulation-probe`, le script analyse les logs/manifests/IR et
n'émet pas les mesures géométriques dédupliquées. Le
[rapport JSON](diagnostics/batch-20260913-221149-features.json) contient les
options du batch, les hashes du rapport source et du probe, les dénominateurs,
les causes détaillées et des exemples. Le
[script d'audit](diagnostics/analyze_batch_features.py) n'écrit que le rapport
demandé ; il ne modifie ni les assets ni le batch audité.

Cet audit mesure la présence des fonctionnalités et les obstacles déclarés.
Il ne constitue pas une nouvelle validation visuelle des 1 799 conversions.
Les critères ci-dessus définissent les vérifications à mener lors de chaque
implémentation.
