if(CONFIG STREQUAL "Release")
    file(MAKE_DIRECTORY "${DESTINATION}")
    file(COPY_FILE "${SOURCE}" "${DESTINATION}/lw_capture.p" ONLY_IF_DIFFERENT)
endif()
