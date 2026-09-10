/* Read UTF-8 object paths from stdin and exercise the tessellator without
   creating derivative packages. Intended for corpus/ASan validation only. */
#include "internal.h"

int main(void) {
    char path[4096]; int failed=0;
    while(fgets(path,sizeof path,stdin)) {
        LWObject object; LWError error={0}; size_t i,faces=0,triangles=0,repeated=0,recovered=0,omitted=0,nonplanar=0;
        path[strcspn(path,"\r\n")]=0;
        if(!lw_load_object(path,&object,&error)) {
            fputs("{\"path\":",stdout); lw_json_string(stdout,path); fputs(",\"error\":",stdout); lw_json_string(stdout,error.message); puts("}"); failed=1; continue;
        }
        fputs("{\"path\":",stdout); lw_json_string(stdout,path); fputs(",\"issues\":[",stdout);
        for(i=0;i<object.primitives.n;i++) {
            const LWPrimitive *p=&object.primitives.v[i]; LWTriangulation t; uint32_t j,k; int has_repeat=0,status;
            if(p->type!=LW_TAG('F','A','C','E')||p->count<3||p->detail_parent!=LW_NONE||p->legacy_surface<0) continue;
            faces++;
            for(j=0;j<p->count;j++) for(k=0;k<j;k++) if(object.indices.v[p->first+j]==object.indices.v[p->first+k]) has_repeat=1;
            repeated+=has_repeat!=0; status=lw_triangulate(&object,p,&t,&error);
            if(status==1) { triangles+=t.corners.n/3; recovered+=has_repeat!=0; nonplanar+=t.nonplanar!=0; }
            else {
                if(omitted++) fputc(',',stdout);
                printf("{\"primitive\":%zu,\"corners\":%u,\"nonplanar\":%d,\"reason\":",i,p->count,t.nonplanar); lw_json_string(stdout,status<0?error.message:t.issue); fputc('}',stdout);
                if(status<0) failed=1;
            }
            lw_free_triangulation(&t);
        }
        printf("],\"faces\":%zu,\"triangles\":%zu,\"repeated_faces\":%zu,\"recovered_repeated_faces\":%zu,\"omitted\":%zu,\"nonplanar\":%zu}\n",faces,triangles,repeated,recovered,omitted,nonplanar);
        lw_free_object(&object);
    }
    return failed;
}
