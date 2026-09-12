# Baking autonome — v0.11.0

Le convertisseur C calcule maintenant un premier profil d'animation FK/IK sans
LightWave, ScreamerNet, plugin de capture, Python embarqué ou SDK à l'exécution.
Le `.bat` active ce calcul par défaut. Il s'agit d'une approximation bornée,
pas encore d'une reproduction du solveur LightWave.

## Reproduction

```powershell
.\convert_content.bat --file smila-by-moebius/Smilla_IK.lws
```

Le batch vérifié est `output/batch-20260912-191746`. Le fichier à ouvrir est :

`packages/smila-by-moebius/gltf/Smilla_IK.lws.gltf`

Conserver son `.bin` adjacent. Le fichier principal contient le maillage, un
skin de 31 joints (30 bones et l'ancre objet), ainsi que les 41 échantillons
des frames 0–40 à 25 fps. Le glTF de l'objet seul reste disponible ; il n'y a
plus de copie `.rig-10000000.gltf` supplémentaire pour cette scène.

La conversion directe ne demande que le binaire C :

```powershell
bin/win64/lwconvert.exe convert content/smila-by-moebius/Smilla_IK.lws --content-root content --output output/smilla-autonomous
```

`--bake-ik off` désactive ce baking et conserve les exports de rigs au repos.
Un `--lightwave-root` explicitement passé au batch sélectionne encore l'oracle
natif ; ce chemin désactive le baking C pour lui fournir des rigs au repos.
Aucun preset n'est enregistré ou rechargé automatiquement.

## Ce qui est conservé et calculé

L'extraction originale précède le baking : `scene.json`, `animation.bin` et
`source.bin` conservent les clés, les paramètres et les octets source. Les
paramètres `KeepGoalWithinReach` sont désormais également indexés dans les
paramètres natifs du nœud. Ils n'étaient auparavant accessibles que dans les
octets source.

Les poses calculées sont une dérivée distincte :

- `baked-animation.json` : profil `autonomous-hpb-ik-0.1`, hash source,
  plage, fréquence, nombre de cibles, corrections de lecture et erreur de cible.
- `baked-animation.bin` : pour chaque échantillon et chaque nœud source,
  TRS local puis mondial ; translation XYZ, quaternion XYZW, échelle XYZ,
  float32 little endian, 80 octets par nœud.
- `manifest.json` : état du calcul, cause d'un refus, chemin vers la dérivée,
  publication dans le glTF principal et indicateur `animated` par rig.

Le profil de poids existant reste indépendant : le choix automatique utilise
les sémantiques LW6 pour LWSC 1/3. Le maillage de contrôle est conservé, sans
subdivision ni animation par morphs. L'objet contenant un seul skin peut être
publié directement comme scène animée ; plusieurs propriétaires de skins
gardent des exports séparés, leur assemblage skinné dans un seul document
restant à faire. Les assemblages rigides utilisent les mêmes poses C.

## Algorithme et périmètre

[`src/ik.c`](../src/ik.c) évalue les enveloppes déjà prises en charge par le
convertisseur (TCB, linéaire, constant/stepped et comportements qualifiés),
puis compose `T × Ry(H) × Rx(P) × Rz(B) × S × T(-pivot)` dans la hiérarchie.
Les contrôleurs 0 suivent les clés ; les axes 3 participent à l'IK lorsque
leur chaîne a une cible `FullTimeIK` active. Une `IKAnchor` arrête la chaîne
et reste en dehors des variables de cette chaîne.

Les cibles partageant une ancre sont résolues ensemble, des ancêtres vers les
descendants, par moindres carrés amortis avec Jacobienne numérique. Les
variables sont les angles H/P/B. Les limites articulaires sont appliquées aux
axes IK, les forces pondèrent les résidus, et les raideurs influencent
l'amortissement. Les angles du précédent échantillon initialisent le suivant ;
le premier part des clés source. Les calculs sont déterministes, bornés à
120 itérations par groupe, 96 axes libres par groupe, 32 cibles, 4096 nœuds,
1001 poses et 64 MiB de poses.

Les anciennes lignes concaténées telles que `HJointStiffness 50PController 3`
sont reconnues par une grammaire précise, sans réécrire la scène originale.
Six de ces concaténations sont récupérées dans Smilla.

Le SDK fourni dans `extern/lwsdk-master` sert de documentation :
`include/lwrender.h` définit les contrôleurs et indicateurs ;
`html/globals/iteminfo.html` décrit limites, raideurs, objectifs et
initialisation. Il ne fournit pas l'algorithme du solveur. Le nouveau solveur
ne compile ni ne copie de code de ce SDK.

Limites explicites de cette première version :

- LWSC 1/3 uniquement ; le support partiel du lecteur LWSC 5 reste inchangé.
- Les pivots orientés, autres contrôleurs, position contrôlée, variantes IK
  supplémentaires et plugins de mouvement/canal bloquent ce profil et sont
  signalés. `smila_run_cycle.lws` est encore refusé pour son pivot orienté.
- Les objectifs portent sur le pivot de l'item. `KeepGoalWithinReach` est
  conservé, mais le solveur C ne déplace pas les cibles source comme peut le
  faire LightWave. Les cibles inaccessibles restent signalées par leur résidu.
- `MatchGoalOrientation` est une pénalité approximative, seulement si l'item
  cible possède lui-même des axes de rotation IK. Sa portée complète reste à
  qualifier, notamment pour les pieds de Smilla.
- La raideur n'est pas une reproduction de la formule native. Le choix entre
  plusieurs poses possibles et les transitions peuvent différer.
- Joint compensation, muscle flexing, morphs et plugins de déformation restent
  omis. Les poids procéduraux restent eux aussi une approximation.
- Le baking suit la plage de lecture source ; sa pose initiale est le premier
  échantillon. `--frame` continue de piloter le snapshot OBJ/les scènes ordinaires.

## QA et mesures

Le batch de référence a été exécuté sans `--lightwave-root`. Le binaire distribué
est `bin/win64/lwconvert.exe`, version 0.11.0.

| Vérification | Résultat |
| --- | --- |
| Validateur Khronos, quatre glTF du batch Smilla IK + run cycle | 0 erreur, 0 avertissement |
| Liens publiés, sources archivées et buffers | Valides |
| Lecture réelle du skin dans Blender 4.2, 41 poses, 11250 sommets exportés | Écart maximum au skin calculé à partir des poses C : environ `8.4e-7` unité |
| Déplacement maximal d'un sommet dans Blender | Environ `0.7384` unité |
| Écart à la cage déformée des captures LW6 sur les 41 poses | RMS `0.07718`, maximum `0.32593` unité |

Le dernier écart inclut les différences de pose IK et de poids. Dans cette scène
métrique, cela représente environ **7,7 cm RMS et 33 cm au maximum**. Les jambes
et les pieds diffèrent sensiblement. Le succès de lecture glTF ne constitue donc
pas une validation de fidélité à LightWave. Le résidu maximal aux cibles source
est `0.17696` unité ; les objectifs de bras peuvent être incompatibles entre eux.

Les captures de référence sont celles de
`_tmp/smilla-lw6-skin-06/IR/Smilla_IK.lws/evaluated-animation/frames`.
Les rendus C aux frames 0, 5, 10, 20, 30 et 40 sont dans
`output/batch-20260912-191746/qa-smilla`.

Rapports : [Blender et écart natif](diagnostics/smilla-autonomous-blender-qa.json),
[Khronos](diagnostics/smilla-autonomous-gltf-validation.json),
[publication](diagnostics/smilla-autonomous-layout.json).

Les dix groupes de régression sont exécutés en Release et avec AddressSanitizer.
Le nouveau groupe vérifie les cibles mobiles par coordonnées indépendantes,
les ancres et parents, les limites et cibles inaccessibles, les clés intactes,
la répétabilité, les buffers glTF, l'export FK, l'IK désactivée, les assemblages
rigides, la publication sans duplication et les refus de paramètres non gérés.

La prochaine qualification doit comparer séparément les transformations, le
déplacement natif des goals, les raideurs et les orientations des pieds, avant
d'étendre aux pivots orientés du run cycle. L'oracle sert à cette mesure ; il
reste absent du chemin de conversion autonome.
