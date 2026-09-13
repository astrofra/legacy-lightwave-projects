# Noms lisibles des textures — audit du 13 septembre 2026

Le préfixe du dossier de la scène est utile, mais ne suffit pas pour les PNG
calculés par le convertisseur. Le même fichier source peut produire plusieurs
images selon le canal, le matériau et l'objet. Une simulation avec des suffixes
lisibles ajoutés uniquement en cas de conflit résout tous les cas observés.
Le nommage est implémenté par défaut depuis la version **0.14.0**. Les mesures
ci-dessous décrivent l'audit préalable ; la commande d'audit reste en lecture seule.
Le [profil des textures](textures.md#readable-texture-names) décrit le fonctionnement livré.

## Périmètre et méthode

Batch terminé : `output/batch-20260913-163728`, statut `partial`.
L'analyse porte sur les sorties publiées de 1 663 conversions, dont 382 scènes,
avec 1 281 IR d'objets. Elle retrouve 2 124 utilisations de textures dans
603 exports OBJ/glTF texturés : 234 contenus PNG distincts. Toutes ces
utilisations ont été rapprochées de leur matériau et de leur IR source.

Un conflit signifie **deux contenus différents affectés au même chemin**,
après normalisation des noms Windows et comparaison sans distinction de casse.
Plusieurs références à des octets identiques ne constituent pas un conflit.
Les PNG exportés sont relus et leur SHA-256 est recalculé.

Le préfixe testé est le nom du dossier contenant la scène pour ses exports,
et celui du dossier contenant le LWO pour un export d'objet seul. Les scènes
Amiga sans extension `.lws` sont incluses. L'arborescence de sortie existante
est conservée : chaque répertoire d'exports possède déjà son dossier `textures`.
Les répertoires OBJ et glTF sont des destinations distinctes.

Le nom de l'image est déduit de la provenance conservée dans l'IR. Pour une
texture composite, l'image principale est choisie dans cet ordre :

| Usage exporté | Priorité des canaux sources |
|---|---|
| Couleur de base, alpha inclus | COLR, DIFF, TRAN |
| Opacité séparée | TRAN, COLR |
| Émission | COLR, LUMI |
| Spéculaire | SPEC |
| Bump | BUMP |
| Normale | NORM |

Une image calculée uniquement à partir de valeurs du matériau prend le nom du
matériau. Ces règles sont une proposition de nommage, pas une reconstruction
du nom original d'un PNG composite qui n'existait pas dans le projet LightWave.
Le périmètre exclut les images non référencées et les textures qui n'ont pas pu
être exportées. Les résultats ne garantissent donc pas l'absence de conflits
sur de futurs projets ou profils de conversion.

## Résultats

Pour les originaux archivés dans les IR d'objets, 322 chemins correspondent à
288 contenus distincts. Dans l'hypothèse d'un dossier d'images commun par projet,
le nom original seul donne cinq collisions ; le préfixe du dossier de la scène
ou de l'objet les élimine dans ce corpus.

Exemples de véritables homonymes :

- `demo-couloir-14/factory/sky.jpg` et `demo-couloir-14/planet/sky.jpg` ;
- `demo-redline-assets-main/map_sol.jpg` et `demo-redline-assets-main/maps/map_sol.jpg`.

Ces paires contiennent des octets différents. Les trois autres noms en conflit
sont dans Redline : `map_gradient_sky.jpg`, `map_red_resto.jpg` et
`map_red_resto_dark.jpg`.

Pour les PNG réellement utilisés dans les exports, le nombre de chemins en
conflit est le suivant. Les composants sont séparés par `__` et suivis de `.png`.

| Nom proposé | glTF | OBJ | Total |
|---|---:|---:|---:|
| Image | 47 | 57 | 104 |
| Dossier + image | 47 | 57 | 104 |
| Dossier + image + usage | 13 | 13 | 26 |
| Dossier + image + matériau + usage | 9 | 9 | 18 |
| Dossier + image + objet + matériau + usage | 0 | 0 | 0 |

Ajouter le dossier ne change pas ce premier décompte : les variantes en conflit
sont déjà dans le même répertoire d'exports et reçoivent le même préfixe.
Un conflit présent en OBJ et en glTF compte deux fois dans la colonne Total.

Trois cas expliquent pourquoi des suffixes supplémentaires sont nécessaires :

- **Aircon** : `aircon_diff.jpg` produit une couleur de base différente pour
  `aircon_target` et `aircon_target_screen`. Le nom du matériau distingue ces
  variantes, même après ajout de l'usage `base_color`.
- **Earth** : `CLOUDS.IFF` produit une carte de couleur et une carte d'opacité
  pour `Atmosphere`. Le suffixe d'usage suffit.
- **Demo Couloir** : `hall.lwo`, `hall2.lwo` et `hall3.lwo` utilisent tous
  `ground.jpg` sur un matériau nommé `murs`, mais produisent trois images de
  couleur différentes. Le nom de l'objet est également nécessaire.

## Proposition de nommage

Commencer par `dossier__image.png`, puis allonger uniquement les noms en conflit :
ajouter l'usage, puis le matériau, puis l'objet. Recontrôler l'ensemble des noms
à chaque étape, car un nom suffixé peut aussi rencontrer le nom naturel d'une
autre image. Pour un conflit résiduel sur un autre corpus, prévoir un suffixe
court stable, vérifié lui aussi, et conserver le SHA-256 complet dans les
métadonnées pour contrôler l'intégrité et reconnaître les contenus identiques.

La simulation de cette progression produit 640 chemins distincts sans conflit :

| Variante finalement nécessaire | Chemins |
|---|---:|
| Dossier + image | 358 |
| Avec usage | 178 |
| Avec matériau et usage | 44 |
| Avec objet, matériau et usage | 60 |

Le nom le plus long de cette simulation fait 80 caractères. Exemples :

```text
aircon__aircon_norm.png
aircon__aircon_diff__aircon_target__base_color.png
aircon__aircon_diff__aircon_target_screen__base_color.png
hall__ground__hall2__murs__base_color.png
```

Il faut aussi fixer la règle des objets partagés : 81 objets sont référencés par
des scènes situées dans plusieurs dossiers. Par exemple, `tv_small.lwo` apparaît
dans les scènes d'`aircon` et de `tv_small`. Son export autonome doit conserver
un nom basé sur son propre dossier ; les copies utilisées dans les exports de
scènes peuvent prendre le préfixe de leur scène. Cela évite de faire dépendre
les noms de l'ordre de conversion des scènes.

L'implémentation coordonne `src/texture_names.c`, les références glTF/MTL et
`tools/texture_names.py`. Le SHA-256 est conservé dans les métadonnées et vérifié
lors de la publication. Le batch attribue les noms définitifs après conversion
de tous les fichiers, afin de traiter les collisions entre conversions distinctes.

## Reproduire l'audit

```powershell
python tools/audit_texture_names.py output/batch-20260913-163728 --report build/texture-naming-audit.json --csv build/texture-naming-audit.csv
```

Le JSON contient les variantes et leurs sources pour chaque conflit, les
résultats par destination, ainsi que des simulations de regroupement par projet
et global. Le CSV contient chaque utilisation et ses cinq noms candidats.
La commande ne renomme aucun fichier et ne nécessite pas LightWave.
