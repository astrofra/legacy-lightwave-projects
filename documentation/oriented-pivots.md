# Pivots orientés et baking autonome — v0.15.0

Le convertisseur C prend en charge `PivotRotation` dans le baking FK/IK,
les transformations des scènes OBJ/glTF et les poses de repos des bones.
Le profil dérivé devient `autonomous-hpb-ik-0.2`. L'exécution reste autonome :
LightWave et le SDK servent uniquement aux mesures de développement.

## Convention mesurée

Pour des vecteurs colonnes, la matrice locale est :

```text
T(position) × R(pivot) × R(animation) × S × T(-pivot_position)
R(HPB) = Ry(H) × Rx(P) × Rz(B)
```

`PivotRotation` et `BoneRestDirection` sont en degrés dans le LWSC ; les
enveloppes de rotation LWSC 3 sont en radians. Le repos remplace les angles
d'animation par `BoneRestDirection`, en conservant la rotation du pivot.
Les deux rotations se composent : leurs angles ne s'additionnent pas.
Le solveur recherche toujours les angles des canaux ; ses axes et ses limites
sont donc évalués dans le repère orienté par le pivot.

Neuf configurations, chacune à deux instants, ont été mesurées dans LightWave
6 et 9.6 : pivot neutre, rotations sur les trois axes, pivot déplacé, échelle
non uniforme, échelle négative, demi-tour, parentage, bone au repos neutre et
bone combinant repos et pivot orientés. Les tests confrontent les poses C aux
matrices natives et les sommets skinnés aux positions déformées natives.
Les écarts restent sous `1e-6` unité pour ces expériences.

Le SDK décrit les paramètres dans
`extern/lwsdk-master/html/globals/iteminfo.html` et la commande
`RecordPivotRotation` dans `html/commands/layout.html`. L'ordre effectif est
établi par les mesures, conservées dans
[la fixture](../tests/fixtures/pivot_rotation_oracle.json), avec les hashes
des sources, captures, programmes et plugins de capture. Aucun code du SDK
n'entre dans le solveur.

Le [script de mesure](../tests/probe_pivot_rotation.py) utilise les vecteurs
natifs du SDK pour ces expériences FK. Il ne prend pas comme référence les
matrices reconstruites par le lecteur Python du protocole LW6 3, qui ne contient
pas la rotation du pivot. Les comparaisons IK de Dialogue ci-dessous utilisent
les captures natives 9.6.

## Résultat sur Dialogue et Smila

Batch produit sans `--lightwave-root` : `output/batch-20260913-181133`.

Le fichier à ouvrir pour le premier personnage est :

```text
packages/quatuor/gltf/work/3d/dialogue01.lws.rig-10000000.gltf
```

Conserver son `.bin` adjacent. Les quatre rigs de Dialogue contiennent chacun
un skin de 80 joints et 60 poses, frames 1 à 60. La scène possède 52 cibles IK :
la limite de 32 cibles est maintenant appliquée à chaque groupe de chaînes,
plutôt qu'à toute la scène. Les limites de taille des groupes restent actives.
Le deuxième personnage est pratiquement immobile, également dans les captures
natives ; cela ne signifie pas que son skin manque.

Les quatre skins sont lus et évalués dans Blender sur les 60 poses. L'écart
maximum avec les poses C et poids exportés est inférieur à `1.28e-6` unité.
La fidélité au solveur LightWave est une mesure distincte :

| Rig Dialogue | Écart maximal à LightWave 9.6, frames 1, 30 et 59 |
| --- | --- |
| `10000000` | 0,000805 unité, environ 0,8 mm |
| `10000013` | 0,001990 unité, environ 2 mm |
| `10000026` | 0,005747 unité, environ 5,7 mm |
| `10000039` | 0,178048 unité, environ 18 cm |

Le quatrième personnage conserve une différence sensible de pose. Son mouvement
calculé varie aussi davantage que dans les captures natives, malgré des cibles
stationnaires. La convergence et le choix des solutions du solveur restent à
améliorer ; la prise en charge des pivots ne rend pas l'IK équivalente à celle
de LightWave. Le résidu maximal aux cibles de cette scène est de `1,28724`
unité ; ce résidu n'est pas l'erreur des sommets du mesh.

`Smilla_IK.lws.gltf` conserve **exactement le même buffer** qu'avant : géométrie,
poids, poses de liaison et animation compris. Les huit buffers des rigs Smila
au repos sont également identiques. Leurs pivots sont désormais acceptés,
mais leurs plugins MotionMixer, expressions et contraintes de mouvement
restent hors du profil autonome. Le manifest indique maintenant ce blocage
au lieu de celui des pivots. Aucun plugin de mouvement n'est ignoré en silence.

Les octets des scènes et objets originaux ainsi que les buffers des clés
originales sont inchangés. Les poses calculées restent une dérivée distincte
dans l'IR. Aucune subdivision n'est ajoutée.

## Validation et reproduction

- 14 conversions partielles, aucun échec ; les autres limites restent signalées.
- 30 glTF publiés : zéro erreur et zéro avertissement Khronos ; liens, hashes
  des sources et textures, tailles des buffers et index de publication valides.
- Blender : quatre skins Dialogue sur 60 poses, Smilla IK sur 41 poses,
  huit rigs Smila au repos ; tous les contrôles de lecture passent.
- Les 11 groupes de régression passent en Release et AddressSanitizer.
  Les nouveaux tests utilisent les observations natives conservées, un cas IK
  dont le pivot réoriente les axes, et 33 groupes indépendants contre un groupe
  dépassant la limite de 32 cibles. Le chemin sans baking couvre aussi les
  échelles signées passant par zéro avec un pivot orienté.

[Rapport complet et hashes](diagnostics/oriented-pivots-qa.json).
Le binaire actualisé est `bin/win64/lwconvert.exe`, version 0.15.0 ; le `.bat`
l'utilise automatiquement.

```powershell
.\convert_content.bat --file quatuor/work/3d/dialogue01.lws --file smila-by-moebius/Smilla_IK.lws
```

Les pivots **déplacés des bones** restent refusés pour la pose de liaison,
ainsi que les variantes déjà non qualifiées : bones de type joint, hiérarchies
invalides et transformations mondiales cisaillées pour le baking TRS.

Les rendus de contrôle locaux sont dans `build/pivot-qa/dialogue-renders/`.
Pour refaire les mesures natives, le script exige un nouveau dossier de sortie :

```powershell
python -X utf8 tests/probe_pivot_rotation.py --output build/pivot-new96 --runtime lightwave96 --lightwave-root _tmp/_extern/LightWave/LW9.6 --capture-plugin bin/win64/lw_capture.p
python -X utf8 tests/probe_skinning_corpus.py content/quatuor/work/3d/dialogue01.lws --content-root content --output build/dialogue-new96 --start 1 --end 59 --step 29
```

Le script de corpus évalue des copies isolées : il désactive la subdivision et
retire les plugins d'affichage ainsi que le bloc vide `LW_LScriptCommander`.
Le rapport natif conserve la liste des adaptations ; les sources ne sont pas
modifiées.
