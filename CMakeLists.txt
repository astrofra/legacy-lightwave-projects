cmake_minimum_required(VERSION 3.21)
project(lightwave_preservation VERSION 0.8.1 LANGUAGES C)

option(LWCONVERT_SANITIZE "Enable address and undefined behavior sanitizers" OFF)
set(LWCONVERT_LIGHTWAVE_SDK "" CACHE PATH "Optional installed LightWave SDK for the native animation capture plugin")
if(MSVC AND NOT LWCONVERT_SANITIZE)
    # Ship Release as one executable, without the Visual C++ runtime DLLs.
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>$<$<NOT:$<CONFIG:Release>>:DLL>")
endif()

add_library(lwconvert_core STATIC
    src/core.c src/platform.c src/lwo.c src/lws.c src/serialize.c src/package.c src/images.c src/raster.c src/textures.c src/obj.c src/triangulate.c src/geometry.c src/normals.c src/skin.c src/gltf.c third_party/libilbm/byterun.c)
add_library(lwconvert_raster STATIC src/stb.c)
target_include_directories(lwconvert_raster PRIVATE third_party/stb)
target_include_directories(lwconvert_core PRIVATE third_party/stb third_party/libilbm)
target_link_libraries(lwconvert_core PUBLIC lwconvert_raster)
target_include_directories(lwconvert_core PUBLIC include PRIVATE src)
target_compile_features(lwconvert_core PUBLIC c_std_17)
set_target_properties(lwconvert_core PROPERTIES C_EXTENSIONS OFF)
add_executable(lwconvert src/main.c)
target_link_libraries(lwconvert PRIVATE lwconvert_core)
target_include_directories(lwconvert PRIVATE src)
if(MSVC)
    target_compile_options(lwconvert_core PRIVATE /W4 /WX /utf-8)
    target_compile_options(lwconvert PRIVATE /W4 /WX /utf-8)
    target_compile_definitions(lwconvert_core PRIVATE _CRT_SECURE_NO_WARNINGS)
    target_compile_definitions(lwconvert PRIVATE _CRT_SECURE_NO_WARNINGS)
else()
    target_compile_options(lwconvert_core PRIVATE -Wall -Wextra -Wpedantic -Werror)
    target_compile_options(lwconvert PRIVATE -Wall -Wextra -Wpedantic -Werror)
    target_link_libraries(lwconvert_core PUBLIC m)
endif()
if(LWCONVERT_SANITIZE)
    if(MSVC)
        # Select Release or RelWithDebInfo: MSVC Debug's /RTC is incompatible.
        target_compile_options(lwconvert_core PUBLIC /fsanitize=address /Zi)
        target_compile_options(lwconvert_raster PRIVATE /fsanitize=address /Zi)
        target_link_options(lwconvert_core PUBLIC /INCREMENTAL:NO)
    else()
        target_compile_options(lwconvert_core PUBLIC -fsanitize=address,undefined -fno-omit-frame-pointer)
        target_compile_options(lwconvert_raster PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
        target_link_options(lwconvert_core PUBLIC -fsanitize=address,undefined)
    endif()
endif()

if(WIN32 AND CMAKE_C_COMPILER_ARCHITECTURE_ID STREQUAL "x64" AND NOT LWCONVERT_SANITIZE)
    # ALL refreshes the copy even if no compilation/linking was necessary.
    add_custom_target(stage_win64 ALL
        COMMAND "${CMAKE_COMMAND}" "-DCONFIG=$<CONFIG>"
            "-DSOURCE=$<TARGET_FILE:lwconvert>"
            "-DDESTINATION=${PROJECT_SOURCE_DIR}/bin/win64"
            "-DTHIRD_PARTY=${PROJECT_SOURCE_DIR}/third_party"
            -P "${PROJECT_SOURCE_DIR}/cmake/StageWin64.cmake"
        DEPENDS lwconvert
        VERBATIM)
endif()
include(CTest)
if(MSVC AND LWCONVERT_LIGHTWAVE_SDK AND NOT LWCONVERT_SANITIZE)
    add_library(lw_capture MODULE tools/lightwave_capture/capture.c)
    target_include_directories(lw_capture PRIVATE "${LWCONVERT_LIGHTWAVE_SDK}/include")
    target_compile_definitions(lw_capture PRIVATE _MSWIN _CRT_SECURE_NO_WARNINGS)
    target_compile_options(lw_capture PRIVATE /W4 /WX /utf-8)
    set_target_properties(lw_capture PROPERTIES SUFFIX .p PREFIX "" MSVC_RUNTIME_LIBRARY MultiThreaded)
    if(CMAKE_C_COMPILER_ARCHITECTURE_ID STREQUAL "x64")
        add_custom_target(stage_capture_win64 ALL
            COMMAND "${CMAKE_COMMAND}" "-DCONFIG=$<CONFIG>" "-DSOURCE=$<TARGET_FILE:lw_capture>"
                "-DDESTINATION=${PROJECT_SOURCE_DIR}/bin/win64"
                -P "${PROJECT_SOURCE_DIR}/cmake/StageCapture.cmake"
            DEPENDS lw_capture VERBATIM)
    endif()
endif()
if(BUILD_TESTING)
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
    add_executable(triangulation_probe tests/triangulation_probe.c)
    target_link_libraries(triangulation_probe PRIVATE lwconvert_core)
    target_include_directories(triangulation_probe PRIVATE src)
    add_test(NAME converter_regression COMMAND "${Python3_EXECUTABLE}" -X utf8
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_converter.py" "$<TARGET_FILE:lwconvert>")
    add_test(NAME batch_regression COMMAND "${Python3_EXECUTABLE}" -X utf8
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_batch.py" "$<TARGET_FILE:lwconvert>")
    add_test(NAME gltf_regression COMMAND "${Python3_EXECUTABLE}" -X utf8
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_gltf.py" "$<TARGET_FILE:lwconvert>")
    add_test(NAME texture_regression COMMAND "${Python3_EXECUTABLE}" -X utf8
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_textures.py" "$<TARGET_FILE:lwconvert>")
    add_test(NAME scene_evaluation_regression COMMAND "${Python3_EXECUTABLE}" -X utf8
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_scene_evaluation.py" "$<TARGET_FILE:lwconvert>")
    add_test(NAME skin_regression COMMAND "${Python3_EXECUTABLE}" -X utf8
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_skin.py" "$<TARGET_FILE:lwconvert>")
    add_test(NAME animation_regression COMMAND "${Python3_EXECUTABLE}" -X utf8
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_animation.py" "$<TARGET_FILE:lwconvert>")
    add_test(NAME normals_regression COMMAND "${Python3_EXECUTABLE}" -X utf8
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_normals.py" "$<TARGET_FILE:lwconvert>")
endif()
install(TARGETS lwconvert RUNTIME DESTINATION bin)
install(FILES third_party/README.md DESTINATION bin/licenses)
install(FILES third_party/libilbm/LICENSE DESTINATION bin/licenses RENAME libilbm.txt)
install(FILES third_party/stb/LICENSE DESTINATION bin/licenses RENAME stb.txt)
