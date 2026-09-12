# Scènes Amiga d'atmobjs — correction v0.8.1

QA du 12 septembre 2026 sur `content/aminet-atmobjs`. Les quatre échecs
signalés sont corrigés : `NastyStation`, `PassMountains`, `StationChase` et
`ThingKills` produisent désormais leur IR, leur OBJ et leur glTF de scène.
Les sources de l'archive restent inchangées.

## Cause des échecs

Ces quatre fichiers sans extension sont des scènes texte **LWSC 1**.
L'identification par signature fonctionnait déjà. Le problème se trouvait
dans l'attribution des blocs d'animation aux éléments de la scène.

Leur caméra unique est implicite. Après les lumières, les fichiers contiennent
`CameraMotion (unnamed)`, sans déclaration `AddCamera`. `ShowCamera 1` apparaît
seulement dans les réglages d'affichage, vers la fin du fichier.

Le lecteur créait une caméra sur `ShowCamera`, ou sur `CameraMotion` seulement
s'il n'avait encore aucun élément courant. Dans ces scènes, l'élément courant
était la dernière lumière, qui possédait déjà ses canaux. Le lecteur tentait
donc de lui ajouter les canaux de la caméra et échouait avec
`duplicate motion block` :

| Scène | Offset d'erreur avant correction |
|---|---:|
| NastyStation | 3698 |
| PassMountains | 2013 |
| StationChase | 6663 |
| ThingKills | 6446 |

Le lecteur sélectionne maintenant le nœud de caméra `0x30000000` lorsqu'il
rencontre `CameraMotion` dans une scène LWSC 1. Il le crée si nécessaire et
réutilise celui créé précédemment par `ShowCamera`. Un `ShowCamera` tardif
ne crée pas de doublon. Il vérifie aussi que chaque bloc `ObjectMotion`,
`LightMotion`, `CameraMotion` ou `BoneMotion` possède un propriétaire du bon
type. Un véritable deuxième bloc de mouvement pour la même caméra reste
une erreur : la correction ne masque pas les doublons.

## Pourquoi les sorties restent partielles

| Entrée | OBJ et glTF produits | Limites principales |
|---|---|---|
| NastyStation | Scène et objets disponibles | `ActualStars`, `Planet` absents ; influences procédurales de cinq bones conservées mais non évaluées |
| PassMountains | Scène avec les montagnes et nuages | Six instances de `Spaceship` absentes |
| Station1 | Objet complet, 10 570 triangles | Textures procédurales `Fractal Noise` et `Dots` non traduites ; 576 faces source non planes |
| StationChase | Scène et objets disponibles | `RandomStars`, trois `SpaceFighter` et trois `NullObject` absents |
| ThingKills | Scène et objets disponibles | `RandomStars`, `Planet` absents ; influences procédurales de six bones actifs conservées mais non évaluées |

Le [README de l'archive](../content/aminet-atmobjs/README) indique explicitement
que ces scènes pour **LightWave 3.0**, créées par Alan Mackey, utilisent certains
objets fournis avec LightWave, dont `space/randomstars` et `space/planet`.
Ces dépendances ne sont présentes ni dans le dossier de l'archive ni dans
l'installation LightWave disponible sous `_tmp/_extern/LightWave`.
Les références originales restent enregistrées comme dépendances non résolues.

`Station1` était donc déjà converti avant ce correctif. Ses 5 397 points et
5 215 polygones natifs sont conservés dans l'IR ; la triangulation produit
10 570 triangles, sans polygone omis ni échec de triangulation. Ses deux
textures procédurales et leurs paramètres, dont la vitesse de `Fractal Noise`,
restent dans l'IR et dans les octets source. Un statut `PARTIAL` demeure
nécessaire pour ces différences de rendu et les faces non planes.

Les déplacements d'objets disponibles sont exportés en glTF : trois pistes
pour `NastyStation`, trois pour `StationChase`, neuf pour `ThingKills`.
`PassMountains` exporte un décor statique : les avions manquent et le mouvement
de caméra, bien conservé dans l'IR, n'est pas encore traduit par le backend
glTF. Ce correctif n'ajoute pas l'export des caméras/lumières ni l'évaluation
des déformations de bones.

## Vérifications

- **152 scènes LWSC 1** inspectées : 148 réussissaient avant, les 152
  réussissent après. Seules les quatre scènes signalées changent de résultat.
- **222 scènes LWSC 3** inspectées : les 222 réussissent avant et après,
  sans changement de diagnostic.
- **270 clés de canaux de caméra** vérifiées directement contre les lignes
  des quatre sources : valeurs, temps, paramètres TCB et interpolation.
  Une seule caméra est présente dans chaque IR, distincte des lumières.
- Batch complet du dossier : **14 fichiers LightWave traités, aucun échec**,
  un `CONVERTED`, treize `PARTIAL` et quatre fichiers auxiliaires `SKIPPED`.
- **16 glTF** validés avec Khronos `2.0.0-dev.3.10` : zéro erreur, zéro
  avertissement, ressources externes incluses.
- **122 tests**, huit suites, réussissent en Release et sous MSVC
  AddressSanitizer. Les nouveaux tests couvrent l'ordre de `ShowCamera`,
  l'absence de lumière, les propriétaires de canaux, les vrais doublons et
  la publication batch des fichiers Amiga sans extension.

Le [rapport machine](diagnostics/aminet-atmobjs-qa.json) conserve les empreintes
des sources et binaires, les diagnostics avant/après, les mesures et les chemins
des fichiers produits. Le test comparatif avant correction utilise le binaire
ASan v0.8.0 archivé temporairement ; après correction, le binaire Release v0.8.1.
Les deux configurations ont ensuite passé la suite complète sur le correctif.

## Reproduction

```powershell
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
python -X utf8 tools/batch_convert.py --content content/aminet-atmobjs --output-root output/aminet-atmobjs-qa
```

Le batch de cette QA est
`output/aminet-atmobjs-qa/batch-20260912-102906/`. Ses fichiers principaux se
trouvent dans `packages/aminet-atmobjs/obj/` et `packages/aminet-atmobjs/gltf/`.
Les scènes sans extension reçoivent les suffixes `.lws.obj` et `.lws.gltf` ;
`Station1` devient `Station1.lwo.obj` et `Station1.lwo.gltf`.

```powershell
build/tools/gltf-validator-2.0.0-dev.3.10/gltf_validator.exe output/aminet-atmobjs-qa/batch-20260912-102906/packages
python -X utf8 documentation/diagnostics/check_atmobjs.py --batch output/aminet-atmobjs-qa/batch-20260912-102906 --report output/aminet-atmobjs-qa/batch-20260912-102906/camera-check.json
```

Le build Release copie automatiquement le convertisseur corrigé dans
`bin/win64/lwconvert.exe`.
