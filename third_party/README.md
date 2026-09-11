# Raster dependencies

- `libilbm`: MIT, Sander van der Burg. The bounded ByteRun1 row decoder is adapted
  from `src/libilbm/byterun.c` at commit
  `586f5822275ef5780509a851cb90c7407b2633d9` of
  https://github.com/svanderburg/libilbm. The source/output bounds, mask-plane
  handling, -128 no-op and per-row validation are local changes. The rest of the
  ILBM decoder uses the published ILBM format and the converter's checked reader.
- `stb_image.h` / `stb_image_write.h`: upstream commit
  `2c980bb59875b0d32144a71867fbdebb2f77cd20` of https://github.com/nothings/stb.
  Used under the MIT option for ordinary raster decoding and PNG writing.

Each subdirectory includes its upstream license. The GPL IFF-ILBM-Parser is not
used. These notices must accompany distributed converter binaries.
