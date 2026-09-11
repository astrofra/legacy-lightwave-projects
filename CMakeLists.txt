cmake_minimum_required(VERSION 3.21)
project(lightwave_preservation VERSION 0.3.0 LANGUAGES C)

option(LWCONVERT_SANITIZE "Enable address and undefined behavior sanitizers" OFF)
if(MSVC AND NOT LWCONVERT_SANITIZE)
    # Ship Release as one executable, without the Visual C++ runtime DLLs.
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>$<$<NOT:$<CONFIG:Release>>:DLL>")
endif()

add_library(lwconvert_core STATIC
    src/core.c src/platform.c src/lwo.c src/lws.c src/serialize.c src/package.c src/images.c src/obj.c src/triangulate.c src/geometry.c src/gltf.c)
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
        target_link_options(lwconvert_core PUBLIC /INCREMENTAL:NO)
    else()
        target_compile_options(lwconvert_core PUBLIC -fsanitize=address,undefined -fno-omit-frame-pointer)
        target_link_options(lwconvert_core PUBLIC -fsanitize=address,undefined)
    endif()
endif()

if(WIN32 AND CMAKE_C_COMPILER_ARCHITECTURE_ID STREQUAL "x64" AND NOT LWCONVERT_SANITIZE)
    # ALL refreshes the copy even if no compilation/linking was necessary.
    add_custom_target(stage_win64 ALL
        COMMAND "${CMAKE_COMMAND}" "-DCONFIG=$<CONFIG>"
            "-DSOURCE=$<TARGET_FILE:lwconvert>"
            "-DDESTINATION=${PROJECT_SOURCE_DIR}/bin/win64"
            -P "${PROJECT_SOURCE_DIR}/cmake/StageWin64.cmake"
        DEPENDS lwconvert
        VERBATIM)
endif()
include(CTest)
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
endif()
install(TARGETS lwconvert RUNTIME DESTINATION bin)
