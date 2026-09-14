# Snow Tanks : plaques de brume opaques — QA du 14 septembre 2026

Le noir dans `large_shot_fprime.lws.gltf` provient de fonctions de rendu
LightWave non interprétées par lwconvert 0.18.0. Les fichiers source et la
texture sont présents ; le matériau glTF exporté est effectivement opaque.
Le problème est donc observable dans le fichier, indépendamment de la
visionneuse Windows.

Lot examiné :
`output/batch-20260914-135413/packages/snow-tanks/`.
Sources : `content/snow-tanks/large_shot_fprime.lws` (LWSC 3) et
`NEW/obj_layered_tiled_fog.lwo` / `NEW/obj_layered_tiled_fog_morph_target.lwo`
(LWO2). `beresina.jpg` sert de référence visuelle ; son rattachement à une
frame de cette scène précise n'a pas été établi.

## Cause principale : transparence additive

Les deux objets ont une surface `layered_fog` avec :

| Réglage natif | Valeur | Export actuel |
| --- | --- | --- |
| `ADTR`, transparence additive | 1, sans enveloppe | Octets archivés dans `source.bin`, sans champ matériel structuré ni application |
| `DIFF`, diffuse | 0 | Texture de couleur dérivée noire |
| `LUMI`, luminosité | 1 | Texture émissive présente |
| Image `COLR` planaire, axe Y | `map_layered_fog.jpg` | Chemin résolu, image décodée et projection appliquée |
| Transparence ordinaire `TRAN` | Absente, valeur par défaut 0 | `alphaMode: OPAQUE`, alpha 255 partout |

`ADTR` se trouve à l'offset **18916**, avec un payload de six octets
`3f8000000000` : flottant 1, enveloppe 0. Le
[SDK LightWave](../extern/lwsdk-master/html/filefmts/lwo2.html#s_ADTR) décrit
une addition de la couleur du matériau à celle du décor situé derrière,
indépendante de la transparence `TRAN`.

La texture native de 128 × 128 est presque noire. L'image émissive exportée
contient des niveaux RGB de **0 à 3 sur 255** : les couches sont conçues pour
ajouter une faible contribution lumineuse. En additif, le noir ne modifie
pas le décor. En opaque, il le cache. Aucune clipmap n'est attachée à ces
deux instances et aucun canal alpha d'image n'a été perdu.

Un `MASK` à 0,5 supprimerait presque toute cette contribution. Le `BLEND`
standard compose avec l'opérateur « over » et atténue le fond selon l'alpha ;
il ne représente pas exactement une addition pure. Il faut donc distinguer
une approximation portable en alpha continu d'un rendu additif natif.
[Spécification glTF, couverture alpha](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#alpha-coverage).

## Deux autres différences confirmées

La cible de morphing porte **`ObjectDissolve 1`**, ligne 1468 de la scène :
elle doit être entièrement invisible comme objet rendu. Ce champ est
explicitement préservé sans évaluation dans l'IR, et la cible est exportée
comme un second mesh opaque. Cela ajoute un deuxième ensemble de plaques.

L'objet de brume principal utilise **`MorphAmount 1` / `MorphTarget 22`**,
lignes 1404–1405. Le glTF ne contient pas cette cible de morphing. Les deux
objets ont chacun **810 points et 640 polygones**, avec un flux d'indices
identique ; leurs 810 positions diffèrent, jusqu'à environ 42,53 unités
locales. Il s'agit donc aussi d'une différence de forme/placement significative,
pas uniquement d'opacité. Cette correspondance topologique fournit un bon
cas de test pour l'export des morph targets LWS.

La scène ajoute un brouillard global (`FogType 2`), de l'éclairage et des
effets de rendu qui ne sont pas reproduits par le matériau glTF. Corriger les
plaques ne suffira donc pas à garantir l'image `beresina.jpg` à l'identique.

## Suite à donner

1. Structurer `ADTR` et son enveloppe dans l'IR, puis définir une approximation
   glTF de la contribution additive, explicitement signalée. Ne pas utiliser
   le nom du fichier ou un seuil de noir pour deviner une clipmap.
2. Appliquer le `ObjectDissolve` statique par instance, notamment la valeur 1,
   sans supprimer les données natives ou les objets utilisés comme cibles.
3. Exporter les morph targets LWS compatibles en conservant les correspondances
   de points et les poids ; commencer ici par le poids constant 1.

La QA n'a modifié ni les assets, ni le lot exporté, ni le convertisseur.
Ces fonctions restent à implémenter. La matrice de fonctionnalités distingue
désormais explicitement la transparence additive de la transparence ordinaire.

## Vérifications reproductibles

Le [rapport d'inspection](diagnostics/snow-tanks-fog-qa.json) enregistre les
empreintes sources/export, les champs natifs, les instances, les images et la
comparaison des positions. Le
[validateur Khronos](diagnostics/snow-tanks-fog-gltf-validation.json) ne relève
**aucune erreur ni avertissement**, seulement deux informations sur les
dimensions des images. Cela valide la structure glTF, pas sa fidélité visuelle.

```powershell
python -X utf8 documentation/diagnostics/check_snow_fog.py --project output/batch-20260914-135413/packages/snow-tanks --source content/snow-tanks --report documentation/diagnostics/snow-tanks-fog-qa.json
```
