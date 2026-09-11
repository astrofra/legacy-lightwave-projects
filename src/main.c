#include "internal.h"
#include <math.h>
#include <errno.h>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

static void help(void) {
    puts("lwconvert " LWCONVERT_VERSION " - standalone LightWave extraction, OBJ and glTF export\n"
         "Usage:\n  lwconvert inspect INPUT\n"
         "  lwconvert convert INPUT --output NEW_DIRECTORY [options]\n"
         "Options:\n  --content-root DIRECTORY  Virtual content root (default: input directory)\n"
         "  --map PREFIX=DIRECTORY    Explicit historic path mapping (repeatable)\n"
         "  --frame NUMBER            OBJ/glTF snapshot frame (default: scene FirstFrame)\n"
         "  --uv-map NAME             Export this native TXUV map, including VMAD seams\n"
         "Output must be outside the content root. Source files are read only.\n"
         "Exit codes: 0 supported subset exported, 1 error, 2 partial export (see manifest).\n"
         "glTF 2.0 exports static base geometry and scalar materials. Blender output is not implemented.");
}
static int run(int argc,char **argv) {
    LWOptions opts={0}; LWError error={0}; int i,result=1; size_t j;
    if(argc==2&&!strcmp(argv[1],"--version")) { puts(LWCONVERT_VERSION); return 0; }
    if(argc<2||(argc==2&&(!strcmp(argv[1],"--help")||!strcmp(argv[1],"-h")))) { help(); return argc<2?1:0; }
    if(argc<3) { help(); return 1; }
    opts.input=lw_absolute(argv[2]);
    if(!opts.input) { lw_error(&error,0,"input","cannot resolve input path"); goto done; }
    if(!strcmp(argv[1],"inspect")&&argc==3) {
        FILE *f=lw_fopen(opts.input,"rb"); char sig[4]; size_t n;
        if(!f) { lw_error(&error,0,"input","cannot open input"); goto done; }
        n=fread(sig,1,4,f); fclose(f);
        if(n==4&&!memcmp(sig,"LWSC",4)) {
            LWScene scene; if(!lw_load_scene(opts.input,&scene,&error)) goto done;
            lw_scene_summary(stdout,&scene); lw_free_scene(&scene);
        } else {
            LWObject object; if(!lw_load_object(opts.input,&object,&error)) goto done;
            lw_object_summary(stdout,&object); lw_free_object(&object);
        }
        result=0; goto done;
    }
    if(strcmp(argv[1],"convert")) { lw_error(&error,0,"arguments","expected inspect or convert"); goto done; }
    for(i=3;i<argc;i+=2) {
        const char *name=argv[i],*value;
        if(i+1==argc) { lw_error(&error,0,"arguments","missing value for %s",name); goto done; }
        value=argv[i+1];
        if(!strcmp(name,"--output")&&!opts.output) opts.output=lw_absolute(value);
        else if(!strcmp(name,"--content-root")&&!opts.root) opts.root=lw_absolute(value);
        else if(!strcmp(name,"--uv-map")&&!opts.uv_map) opts.uv_map=lw_dup(value);
        else if(!strcmp(name,"--frame")&&!opts.frame_set) {
            char *end; errno=0; opts.frame=strtod(value,&end);
            if(end==value||*end||errno||!isfinite(opts.frame)) { lw_error(&error,0,"arguments","invalid frame number"); goto done; }
            opts.frame_set=1;
        } else if(!strcmp(name,"--map")) {
            const char *equal=strchr(value,'='); LWRule rule={0}; size_t length,k;
            if(!equal||equal==value||!equal[1]) { lw_error(&error,0,"arguments","expected --map PREFIX=DIRECTORY"); goto done; }
            length=(size_t)(equal-value); rule.prefix=malloc(length+1); rule.destination=lw_absolute(equal+1);
            if(!rule.prefix||!rule.destination) { free(rule.prefix); free(rule.destination); lw_error(&error,0,"allocation","cannot allocate/resolve path mapping"); goto done; }
            memcpy(rule.prefix,value,length); rule.prefix[length]=0;
            for(k=0;k<length;k++) if(rule.prefix[k]=='\\') rule.prefix[k]='/';
            if(!LW_ADD(opts.rules,rule,&error)) { free(rule.prefix); free(rule.destination); goto done; }
        } else { lw_error(&error,0,"arguments","unknown or duplicate option: %s",name); goto done; }
    }
    if(!opts.output) { lw_error(&error,0,"arguments","--output NEW_DIRECTORY is required"); goto done; }
    if(!opts.root) opts.root=lw_dirname(opts.input);
    if(!opts.root) { lw_error(&error,0,"allocation","cannot allocate content root"); goto done; }
    result=lw_convert(&opts,&error); if(result<0) result=1;
done:
    if(result==1) fprintf(stderr,"lwconvert: %s at byte %zu: %s\n",error.context,error.offset,error.message);
    free(opts.input); free(opts.root); free(opts.output); free(opts.uv_map);
    for(j=0;j<opts.rules.n;j++) { free(opts.rules.v[j].prefix); free(opts.rules.v[j].destination); }
    LW_FREE(opts.rules); return result;
}
#ifdef _WIN32
int wmain(int argc,wchar_t **wide) {
    char **args=calloc((size_t)argc,sizeof *args); int i,result=1;
    if(!args) return 1;
    for(i=0;i<argc;i++) {
        int length=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,wide[i],-1,NULL,0,NULL,NULL);
        if(!length) goto done;
        args[i]=malloc((size_t)length); if(!args[i]) goto done;
        if(!WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,wide[i],-1,args[i],length,NULL,NULL)) goto done;
    }
    result=run(argc,args);
done:
    for(i=0;i<argc;i++) free(args[i]);
    free(args); return result;
}
#else
int main(int argc,char **argv) { return run(argc,argv); }
#endif
