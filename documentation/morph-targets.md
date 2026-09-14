# Morph targets autonomes — profil 0.19.0

Depuis `lwconvert 0.19.0`, le convertisseur C traduit les déformations morph
prises en charge sans lancer LightWave. Les données d'origine restent dans
le LWIR et `source.bin`; le glTF contient une représentation dérivée standard.

## Données prises en charge

- les maps continues LWO2 `MORF`, interprétées comme des déplacements relatifs;
- les maps continues LWO2 `SPOT`, interprétées comme des positions absolues;
- `LW_MorphMixer`, avec le nom, la valeur courante et l'enveloppe de chaque
  `MorfForm`;
- `MorphTarget` entre deux objets ayant exactement le même nombre de points,
  les mêmes indices et les mêmes primitives;
- `MorphAmount`, constant ou animé;
- les poids morph et le skinning dans un même glTF. Selon l'ordre défini par
  glTF 2.0, la cible morph est appliquée avant le skinning.

Ces conventions suivent la documentation du SDK présent dans le dépôt :
[`MORF` contient des deltas et `SPOT` des positions alternatives](../extern/lwsdk-master/html/filefmts/lwo2.html),
tandis que [`MorphAmount` est normalement compris entre 0 et 1](../extern/lwsdk-master/html/commands/layout.html).

Les points absents d'une map `MORF` ou `SPOT` ne bougent pas. Après
triangulation et séparation des coutures UV/normales, chaque copie d'un point
reçoit le même déplacement. Les normales lissées sont recalculées pour chaque
cible et exportées comme deltas `NORMAL`. Les positions, maps, clés et
paramètres natifs ne sont pas remplacés par ces dérivés.

Dans `scene.json`, chaque nœud possède maintenant `morph_deformation`, qui
contient la cible externe éventuelle, `MorphSurfaces`, `MTSEMorphing`, la valeur
ou l'enveloppe `MorphAmount`, ainsi que les formes de `LW_MorphMixer`. Dans le
glTF, les noms sont placés dans `mesh.extras.targetNames`, les deltas dans
`primitive.targets`, les valeurs initiales dans `node.weights` et l'animation
dans un canal dont le chemin est `weights`.

## Visibilité des objets cibles

Un objet utilisé comme cible peut aussi exister comme item de scène. Une valeur
statique `ObjectDissolve` supérieure ou égale à 1 masque maintenant cette
instance dans le glTF, tout en conservant le nœud, l'objet autonome et sa source
dans le package. Les valeurs partielles et les enveloppes de dissolve restent
préservées sans traduction.

## Limites explicites

- `MorphSurfaces` n'anime pas encore les matériaux;
- `MTSEMorphing` n'est pas assimilé à un morph linéaire simple;
- les `VMAD` de type `MORF` ou `SPOT` ne sont pas traduites;
- les modificateurs d'enveloppe inconnus bloquent la piste concernée;
- une cible objet dont la topologie ou l'ordre des points diffère est refusée;
- les poids sont échantillonnés une fois par frame source et interpolés
  linéairement dans le glTF;
- les tangentes d'une normal map restent celles de la cage au repos. Les deltas
  de normales géométriques sont présents, mais une forte déformation peut
  demander un rebake de tangentes dans un DCC.

Ces cas incrémentent `gltf_morph_issues` au lieu de produire silencieusement une
cible possiblement fausse.

## Cas de QA

`snow-tanks/large_shot_fprime.lws` vérifie une cible objet externe à poids 1,
avec l'item cible dissous. Le glTF résultant contient une cible et une seule
instance de géométrie visible pour la paire source/cible.

`demo-redline-assets-main/01_seq_stonehenge_01.lws` vérifie les 23 maps
`m02` à `m24` et une piste `weights` issue de `LW_MorphMixer`.
`03_01_seq_01_ride.lws` combine une cible objet animée et plusieurs instances
du maillage à 23 formes.

Le batch de QA du 14 septembre couvre Snow Tanks et l'intégralité de Red Line.
[Son audit glTF](diagnostics/morph-gltf-validation.json) compte 54 documents
avec morphs, dont 41 animés et 89 canaux `weights`. Les 103 occurrences de
`LW_MorphMixer` rencontrées sont interprétées. Les 54 documents passent le
validateur Khronos avec zéro erreur et zéro avertissement; les 214 informations
concernent uniquement des textures historiques aux dimensions non puissance de
deux. Onze manifests signalent encore une limite morph explicite, telle que
`MorphSurfaces` ou `MTSEMorphing`.

[L'import de contrôle dans Blender 4.2](diagnostics/morph-blender-qa.json)
retrouve 23 shape keys animées sur Stonehenge, quatre jeux de 23 sur Pueblo et
la cible objet statique de Snow Tanks avec son poids non nul. Ce contrôle prouve
l'interopérabilité des données; il ne constitue pas encore une comparaison
pixel à pixel avec un rendu LightWave.

Les régressions synthétiques couvrent aussi `MORF` sparse, `SPOT` absolu,
animation en secondes LWSC3, cible dissoute et présence simultanée de
`JOINTS_n`/`WEIGHTS_n` avec les cibles morph.
