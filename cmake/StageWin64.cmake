# Only the distributable configuration may replace the checked-in executable.
if(NOT CONFIG STREQUAL "Release")
    return()
endif()
file(MAKE_DIRECTORY "${DESTINATION}")
file(COPY_FILE "${SOURCE}" "${DESTINATION}/lwconvert.exe" ONLY_IF_DIFFERENT)
message(STATUS "Win64 converter: ${DESTINATION}/lwconvert.exe")
