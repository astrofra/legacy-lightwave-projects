# legacy-lightwave-projects
My collection of personal Lightwave 3D objects and scenes, gathered since my early Amiga years.

## Convertisseur C — premier jalon

`lwconvert` lit les objets LWOB/LWO2, les presets PST_ et les scènes LWS 1/3.
Il produit un paquet LWIR conservant les sources, accompagné d'exports OBJ/MTL.
Les sorties glTF 2.0 et Blender sont prévues pour les prochains jalons.
Le convertisseur fonctionne sans Blender, sans addon et sans interface graphique.

Compilation Windows avec CMake et Visual Studio 2022 :

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
```

Python 3 est utilisé pour les tests ; `-DBUILD_TESTING=OFF` permet de compiler
uniquement le programme C. Le binaire est `build/Release/lwconvert.exe`.

```powershell
build/Release/lwconvert.exe inspect content/metropolis-robots/metropolis_model_UV.lwo

# Le dossier parent de la sortie doit exister ; la sortie elle-même doit être nouvelle.
New-Item -ItemType Directory -Force output
build/Release/lwconvert.exe convert content/metropolis-robots/metropolis_model_UV.lwo --content-root content/metropolis-robots --output output/metropolis --uv-map st

build/Release/lwconvert.exe convert content/circus/Mr_Lector_2.lws --content-root content/circus --output output/lector --frame 1
```

Le code de sortie **2** signifie qu'un paquet a été produit avec des limites
signalées dans `manifest.json` : textures non exportées, UV manquants, couches
introuvables, etc. **0** valide le sous-ensemble pris en charge ; **1** indique
une erreur. Une scène produit les OBJ de ses objets résolus et, si les
transformations sont évaluables, un `scene.obj` à la frame demandée.

Voir le [guide du convertisseur](documentation/converter.md) pour le format du
paquet, la résolution des chemins et les limites de cette version, ainsi que le
[diagnostic de faisabilité](documentation/lightwave-to-blender-feasibility.md)
pour l'architecture des trois sorties.
