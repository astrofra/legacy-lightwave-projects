/* Adapted from libilbm/src/libilbm/byterun.c, ILBM_unpackByteRun.
 * Copyright (c) 2012 Sander van der Burg. MIT; see LICENSE alongside this file.
 * Local changes: bounded input/output, row boundaries, explicit error return,
 * and ByteRun1 -128 no-op. No dependency on the unbounded libiff parser. */
#include "byterun.h"
#include <string.h>
int ilbm_unpack_row(const unsigned char *data,size_t size,size_t *position,unsigned char *out,size_t length) {
    size_t written=0;
    while(written<length) {
        int control; size_t count;
        if(*position>=size) return 0;
        control=data[(*position)++]; if(control>127) control-=256;
        if(control==-128) continue;
        count=control>=0?(size_t)control+1:(size_t)(1-control);
        if(count>length-written) return 0;
        if(control>=0) {
            if(count>size-*position) return 0;
            memcpy(out+written,data+*position,count); *position+=count;
        } else {
            if(*position>=size) return 0;
            memset(out+written,data[(*position)++],count);
        }
        written+=count;
    }
    return 1;
}
