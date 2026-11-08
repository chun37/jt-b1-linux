#include <linux/fb.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static void die(const char *s) { perror(s); exit(1); }
int main(int argc,char **argv) {
    setbuf(stdout,NULL);
    int fd=open("/dev/fb0",O_RDWR); if(fd<0) die("open fb0");
    struct fb_fix_screeninfo f; struct fb_var_screeninfo v;
    if(ioctl(fd,FBIOGET_FSCREENINFO,&f)) die("GET_FIX");
    if(ioctl(fd,FBIOGET_VSCREENINFO,&v)) die("GET_VAR");
    printf("id=%.16s smem=%lx len=%u stride=%u type=%u visual=%u\n",f.id,f.smem_start,f.smem_len,f.line_length,f.type,f.visual);
    printf("res=%ux%u virtual=%ux%u offset=%u,%u bpp=%u rotate=%u nonstd=%u activate=%u\n",v.xres,v.yres,v.xres_virtual,v.yres_virtual,v.xoffset,v.yoffset,v.bits_per_pixel,v.rotate,v.nonstd,v.activate);
    printf("rgba=%u:%u %u:%u %u:%u %u:%u\n",v.red.offset,v.red.length,v.green.offset,v.green.length,v.blue.offset,v.blue.length,v.transp.offset,v.transp.length);
    if(argc==1) return 0;
    if(argc!=3 || (strcmp(argv[1],"--present") && strcmp(argv[1],"--reconfigure")) || v.xres!=1024 || v.yres!=600 || v.bits_per_pixel!=32 || f.line_length!=4096 || f.smem_len<2457600 || v.red.offset!=16 || v.green.offset!=8 || v.blue.offset!=0) return 2;
    int image=open(argv[2],O_RDONLY|O_NOFOLLOW); struct stat st;
    if(image<0 || fstat(image,&st) || !S_ISREG(st.st_mode) || st.st_size!=2457600) die("input geometry");
    void *p=mmap(NULL,2457600,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0); if(p==MAP_FAILED) die("mmap fb0");
    size_t n=0;
    while(n<2457600) { ssize_t r=read(image,(char*)p+n,2457600-n); if(r<=0) die("read image"); n+=r; }
    close(image); __sync_synchronize();
    v.xoffset=0; v.yoffset=0; v.activate=FB_ACTIVATE_NOW|FB_ACTIVATE_FORCE;
    if(!strcmp(argv[1],"--reconfigure")) {
        puts("mapped image copied; reapplying unchanged mode via FBIOPUT_VSCREENINFO");
        if(ioctl(fd,FBIOPUT_VSCREENINFO,&v)) die("PUT_VAR");
        puts("PUT_VAR success");
    }
    printf("invoking FBIOPAN_DISPLAY\n");
    if(ioctl(fd,FBIOPAN_DISPLAY,&v)) die("PAN_DISPLAY");
    puts("PAN_DISPLAY success");
    munmap(p,2457600); close(fd); return 0;
}
