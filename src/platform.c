#ifndef _WIN32
#define _XOPEN_SOURCE 700
#endif
#include "internal.h"
#include <errno.h>
#include <limits.h>
#include <ctype.h>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

#ifdef _WIN32
static wchar_t *wide(const char *s) {
    int n=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s,-1,NULL,0); wchar_t *w;
    if(!n) return NULL;
    w=malloc((size_t)n*sizeof *w);
    if(w&&!MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s,-1,w,n)) { free(w); return NULL; }
    return w;
}
static char *utf8(const wchar_t *w) {
    int n=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,w,-1,NULL,0,NULL,NULL); char *s;
    if(!n) return NULL;
    s=malloc((size_t)n);
    if(s&&!WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,w,-1,s,n,NULL,NULL)) { free(s); return NULL; }
    return s;
}
#endif
FILE *lw_fopen(const char *path,const char *mode) {
#ifdef _WIN32
    wchar_t *w=wide(path),*m=wide(mode); FILE *f=NULL;
    if(w&&m) f=_wfopen(w,m);
    free(w); free(m); return f;
#else
    return fopen(path,mode);
#endif
}
static void slashes(char *s) { for(;*s;s++) if(*s=='\\') *s='/'; }
char *lw_join(const char *base,const char *name) {
    size_t a=strlen(base),b=strlen(name); char *p;
    if(a>SIZE_MAX-b-2) return NULL;
    p=malloc(a+b+2); if(!p) return NULL;
    memcpy(p,base,a); if(a&&base[a-1]!='/'&&base[a-1]!='\\') p[a++]='/';
    memcpy(p+a,name,b+1); slashes(p); return p;
}
const char *lw_basename(const char *s) {
    const char *p=s; for(;*s;s++) if(*s=='/'||*s=='\\') p=s+1; return p;
}
char *lw_dirname(const char *s) {
    char *p=lw_dup(s),*last; size_t n;
    if(!p) return NULL;
    slashes(p); last=strrchr(p,'/');
    if(!last) { free(p); return lw_dup("."); }
    n=(size_t)(last-p);
    if(!n || (n==2&&p[1]==':')) last[1]=0; else *last=0;
    return p;
}
char *lw_absolute(const char *s) {
#ifdef _WIN32
    wchar_t *w=wide(s),*result; DWORD n; char *p;
    if(!w) return NULL;
    n=GetFullPathNameW(w,0,NULL,NULL); if(!n) { free(w); return NULL; }
    result=malloc((size_t)n*sizeof *result); if(!result) { free(w); return NULL; }
    if(!GetFullPathNameW(w,n,result,NULL)) { free(w); free(result); return NULL; }
    p=utf8(result); free(w); free(result); if(p) slashes(p); return p;
#else
    char *p=realpath(s,NULL),*parent,*resolved,*joined;
    if(p) return p;
    /* Nonexistent output paths are resolved against an existing parent. */
    parent=lw_dirname(s); if(!parent) return NULL;
    resolved=realpath(parent,NULL); free(parent); if(!resolved) return NULL;
    joined=lw_join(resolved,lw_basename(s)); free(resolved); return joined;
#endif
}
int lw_path_exists(const char *p) {
#ifdef _WIN32
    wchar_t *w=wide(p); DWORD attr;
    if(!w) return 0;
    attr=GetFileAttributesW(w); free(w); return attr!=INVALID_FILE_ATTRIBUTES;
#else
    struct stat st; return lstat(p,&st)==0;
#endif
}
int lw_path_equal(const char *a,const char *b) {
#ifdef _WIN32
    wchar_t *x=wide(a),*y=wide(b); int same=0;
    if(x&&y) same=CompareStringOrdinal(x,-1,y,-1,TRUE)==CSTR_EQUAL;
    free(x); free(y); return same;
#else
    /* Exact Unicode spelling, ASCII case-insensitivity for historical names. */
    while(*a&&*b) { if(tolower((unsigned char)*a)!=tolower((unsigned char)*b)) return 0; a++; b++; }
    return *a==*b;
#endif
}
int lw_path_inside(const char *path,const char *root) {
    size_t n=strlen(root),len=strlen(path); char *prefix; int same;
    while(n&&root[n-1]=='/') n--;
    if(len<n || (len>n&&path[n]!='/')) return 0;
    prefix=malloc(n+1); if(!prefix) return 1; /* conservative on allocation failure */
    memcpy(prefix,path,n); prefix[n]=0;
    { char *trimmed=lw_dup(root); if(!trimmed) { free(prefix); return 1; }
      trimmed[n]=0; same=lw_path_equal(prefix,trimmed); free(trimmed); }
    free(prefix); return same;
}
int lw_mkdir(const char *path,LWError *e) {
#ifdef _WIN32
    wchar_t *w=wide(path); int ok=w&&CreateDirectoryW(w,NULL);
    free(w);
#else
    int ok=mkdir(path,0777)==0;
#endif
    return ok ? 1 : lw_error(e,0,"directory","cannot create new directory %s",path);
}
static int walk(const char *path,LWPaths *paths,LWError *e,unsigned depth) {
    if(depth>128) return lw_error(e,0,"discovery","directory depth exceeds 128: %s",path);
#ifdef _WIN32
    {
        char *pattern=lw_join(path,"*"); wchar_t *w=pattern?wide(pattern):NULL;
        WIN32_FIND_DATAW entry; HANDLE h; int ok=1;
        free(pattern); if(!w) return lw_error(e,0,"discovery","out of memory");
        h=FindFirstFileW(w,&entry); free(w);
        if(h==INVALID_HANDLE_VALUE) return lw_error(e,0,"discovery","cannot enumerate %s",path);
        do {
            char *name,*child;
            if(!wcscmp(entry.cFileName,L".")||!wcscmp(entry.cFileName,L"..")) continue;
            if(entry.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT) continue;
            name=utf8(entry.cFileName); child=name?lw_join(path,name):NULL; free(name);
            if(!child) { ok=lw_error(e,0,"discovery","out of memory"); break; }
            if(entry.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY) { ok=walk(child,paths,e,depth+1); free(child); }
            else { ok=LW_ADD(*paths,child,e); if(!ok) free(child); }
            if(!ok) break;
        } while(FindNextFileW(h,&entry));
        if(ok && GetLastError()!=ERROR_NO_MORE_FILES) ok=lw_error(e,0,"discovery","enumeration failed: %s",path);
        FindClose(h); return ok;
    }
#else
    {
        DIR *dir=opendir(path); struct dirent *entry; int ok=1;
        if(!dir) return lw_error(e,0,"discovery","cannot enumerate %s",path);
        errno=0;
        while((entry=readdir(dir))!=NULL) {
            char *child; struct stat st;
            if(!strcmp(entry->d_name,".")||!strcmp(entry->d_name,"..")) continue;
            child=lw_join(path,entry->d_name);
            if(!child) { ok=lw_error(e,0,"discovery","out of memory"); break; }
            if(lstat(child,&st)!=0) { free(child); ok=lw_error(e,0,"discovery","cannot stat entry"); break; }
            if(S_ISDIR(st.st_mode)) { ok=walk(child,paths,e,depth+1); free(child); }
            else if(S_ISREG(st.st_mode)) { ok=LW_ADD(*paths,child,e); if(!ok) free(child); }
            else free(child);
            if(!ok) break;
            errno=0;
        }
        if(ok&&errno) ok=lw_error(e,0,"discovery","enumeration failed: %s",path);
        closedir(dir); return ok;
    }
#endif
}
static int path_sort(const void *a,const void *b) { return strcmp(*(const char *const *)a,*(const char *const *)b); }
int lw_walk(const char *path,LWPaths *paths,LWError *e) { LW_TRY(walk(path,paths,e,0)); if(paths->n) qsort(paths->v,paths->n,sizeof *paths->v,path_sort); return 1; }
void lw_free_paths(LWPaths *p) { size_t i; for(i=0;i<p->n;i++) free(p->v[i]); LW_FREE(*p); }
int lw_read_source(const char *path,LWSource *s,LWError *e) {
    FILE *f=lw_fopen(path,"rb"); long n;
    memset(s,0,sizeof *s);
    if(!f) return lw_error(e,0,"input","cannot open %s",path);
    if(fseek(f,0,SEEK_END)||(n=ftell(f))<0||n>512L*1024L*1024L||fseek(f,0,SEEK_SET)) {
        fclose(f); return lw_error(e,0,"input","cannot size input or input exceeds 512 MiB: %s",path);
    }
    s->data=malloc((size_t)n+1); s->path=lw_dup(path); s->size=(size_t)n;
    if(!s->data||!s->path) { fclose(f); lw_free_source(s); return lw_error(e,0,"input","out of memory"); }
    if(s->size && fread(s->data,1,s->size,f)!=s->size) { fclose(f); lw_free_source(s); return lw_error(e,0,"input","short read: %s",path); }
    fclose(f); s->data[s->size]=0; lw_sha256(s->data,s->size,s->sha256); return 1;
}
void lw_free_source(LWSource *s) { free(s->path); free(s->data); memset(s,0,sizeof *s); }
