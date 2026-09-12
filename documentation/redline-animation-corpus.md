# Redline : corpus FK, IK et morphs pour la roadmap

**Inventaire structurel du 12 septembre 2026.** Le dossier fourni est
`content/demo-redline-assets-main/`. Il devient un corpus réel de la
[roadmap IK et oracle](lightwave-ik-oracle-feasibility.md), avec un volet distinct
pour les morphs et des cas combinant plusieurs mécanismes.

Cette passe lit les sources et les chunks des objets. Elle n'exécute pas les
scènes, ne qualifie pas leur conversion et ne modifie pas les assets.
L'[inventaire JSON](diagnostics/redline-animation-inventory.json) contient les
empreintes, déclarations, cartes de morph, doublons et indices de dépendances
manquantes. Pour le régénérer depuis la racine du dépôt :

```powershell
python -X utf8 documentation/diagnostics/scan_redline_animation.py
```

## Inventaire mesuré

| Élément | Nombre |
| --- | ---: |
| Fichiers | 293 |
| Scènes `.lws` | 93, dont 71 contenus distincts par SHA-256 |
| Objets `.lwo` | 119, dont 100 contenus distincts par SHA-256 |
| Scènes avec contrôleurs H/P/B explicitement en mode 3 (IK) | 9 |
| Scènes avec `MorphTarget` | 15 |
| Scènes chargeant `LW_MorphMixer` | 40 |
| Scènes avec valeurs de clés variables dans des enveloppes `MorfForm` | 38 |
| Fichiers objets contenant des cartes `MORF` | 5, soit 3 contenus distincts |
| Cartes de morph recensées, doublons de fichiers inclus | 102 |
| Scènes déclarant `AddBone` | 0 |

Ces catégories se recouvrent. La présence d'un contrôleur ou d'une enveloppe
décrit le fichier ; l'activation effective, les dépendances et la déformation
doivent encore être vérifiées. Huit scènes comportent d'autres contrôleurs de
rotation non nuls : elles ne sont pas comptées comme IK sur cette seule base.

Les morphs ne reposent donc pas uniquement sur des noms de fichiers :
`mesh_pueblo_trail.lwo` contient 23 cartes `MORF`, et
`mesh_redlines_central.lwo` en contient 10. Le scanner FORM ne signale pas
d'anomalie sur les 119 objets inspectés ; cela ne valide pas l'équivalence des
topologies entre cibles de morph ni leur association à chaque scène.

## Premiers cas à qualifier

Les chemins ci-dessous sont relatifs à `content/demo-redline-assets-main/`.

| Cas | Constat dans les sources | Usage proposé |
| --- | --- | --- |
| `seq_stonehenge_03.lws`, `seq_takeoff_02.lws` | Clés variables ; aucun contrôleur IK, `MorphTarget` ou plugin déclaré | Contrôles de cinématique directe et de lecture glTF pour J2 ; réserver une des scènes à la validation |
| `seq_planet_01.lws` | 10 déclarations de contrôleurs IK, 4 cibles ; aucun MorphMixer | Variante réelle pour J4 après regroupement par famille de rig |
| `scene_robots.lws` | Même nombre de contrôleurs/cibles, avec 1 MorphMixer et 23 enveloppes de morph variables | Test d'intégration IK + morph, après qualification séparée des deux mécanismes |
| `work_trail.lws` | Un objet chargé, 1 MorphMixer, 23 enveloppes de morph variables | Premier cas pour capturer et comparer des endomorphs animés |
| `work_pueblo_morphmix.lws` | 23 enveloppes de morph, toutes à valeurs de clés constantes | Contrôle du chargement des cartes et de la pose de base ; pas une preuve d'animation de morph |
| `03_01_seq_01_ride.lws` | `MorphTarget`, `MorphSurfaces 1` et 3 MorphMixer | Validation combinée FK/morph, avec frontière explicite entre géométrie et surfaces |
| `work_pueblo_morph.lws` | Chaîne de 4 `MorphTarget`, `MTSEMorphing 1`, enveloppe `MorphAmount` allant de 0 à 4 | Cas de morph séquentiel à conserver, mais dépendances à retrouver avant qualification |
| `sceneswork/scene_copter_test.lws` | Contrôleurs de ciblage, 20 `LW_Follower`, masters de scène | Profil procédural distinct ; ce n'est pas un contrôle FK simple |

Le cas `work_trail.lws` est un meilleur départ pour l'animation des endomorphs que
`work_pueblo_morphmix.lws`, dont les poids déclarés ne varient pas. Les limites
de lecture des enveloppes devront être testées aux clés et entre les clés.

## Dépendances et indépendance des essais

Les neuf scènes IK présentent chacune quatre `GoalObject` et six lignes
concaténant une raideur et un contrôleur, comme le robot Carrot. Plusieurs
réutilisent des pièces `mesh_bot_*`. Ce sont de bons tests d'intégration, mais
leur indépendance vis-à-vis du rig déjà étudié ne doit pas être présumée.
Regrouper les scènes par rig, objets, animation et provenance avant de figer
le partage entre ajustement et validation. Les duplications exactes dans `fra/`,
`temp/` et `sceneswork/` sont identifiées dans le rapport ; les variantes proches
nécessitent encore un examen.

Onze scènes référencent au moins un nom de fichier objet absent de tout ce
corpus. En particulier, `work_pueblo_morph.lws` et `scene_pueblo_show_01.lws`
référencent `mesh_m00.lwo`, `mesh_m01.lwo`, `mesh_m02.lwo`, `mesh_m03.lwo` et
`mesh_m05.lwo`, qui n'y figurent pas. `scene_copter_test.lws` référence
`mesh_copter.lwo`, également absent. Cette vérification porte sur les noms ;
elle ne remplace pas la résolution des chemins, couches et versions d'objets.

Des scènes de travail déclarent `.SpreadsheetStandardBanks` et
`SpreadsheetSceneManager`. Leur traitement par le pont natif doit être qualifié
avant capture ; l'inventaire ne démontre pas qu'elles s'exécutent déjà dans
l'installation LightWave actuelle. De même, une bibliothèque d'expressions
présente dans le texte peut contenir des entrées inutilisées.

## Volet morph de la roadmap

Ce volet peut commencer après la qualification de l'oracle et des conventions
de transformation, sans attendre la résolution complète de l'IK.

1. **R1 — Préparer le corpus.** Résoudre les objets et couches des cas retenus,
   regrouper les doublons et rigs apparentés, puis fixer les scènes réservées.
   Exécuter les contrôles FK via le convertisseur et capturer les références
   natives avec la subdivision désactivée.
2. **R2 — Cartes et poids.** Sur `work_trail.lws`, relier les noms `MorfForm`
   aux cartes `MORF` et aux couches, conserver les enveloppes dans l'IR, puis
   comparer les positions déformées à l'oracle. Vérifier le mélange de plusieurs
   cartes et l'ordre des transformations.
3. **R3 — glTF autonome.** Exporter les cibles nommées et leurs pistes `weights`,
   avec correspondance vérifiée entre sommets sources et sommets triangulés.
   Comparer la déformation aux clés et aux temps intermédiaires sur une scène
   réservée. L'activation du profil exige ces mesures et une conversion sans
   LightWave disponible. Le modèle de cibles et poids est décrit par
   [glTF 2.0](https://github.com/KhronosGroup/glTF/blob/main/specification/2.0/Specification.adoc#morph-targets).
4. **R4 — Morphs entre objets et combinaison.** Qualifier `MorphTarget` et la
   correspondance des points sur Ride, puis le cas MTSE lorsque ses objets seront
   disponibles. Conserver `MorphSurfaces` et les valeurs originales ; une valeur
   `MorphAmount` de 4 ne doit pas être ramenée arbitrairement à 1. Tester ensuite
   IK + morph sur `scene_robots.lws`, lorsque les deux profils sont qualifiés.

Le pont rigide actuel vérifie que chaque cage suit une transformation d'objet.
Une déformation morph active sort de cette condition : le succès du robot Carrot
ne démontre donc pas celui de ces scènes. Le profil Smila capture déjà des
déformations, mais son existence ne qualifie pas automatiquement ce corpus ni
un export autonome de ses cibles nommées.

Le [README de Redline](../content/demo-redline-assets-main/README.md) conserve
les crédits, notamment Fra et med pour la 3D. Le dossier contient un
[fichier de licence CC0 1.0](../content/demo-redline-assets-main/LICENSE).
Conserver ces documents et la provenance lors de la création des fixtures.
