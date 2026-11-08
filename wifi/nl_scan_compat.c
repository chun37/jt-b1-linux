/* Process-local compatibility shim for stock JT-B1 nl80211 (maxattr 158).
 * New userspace scan flags are rejected by this 2014 backport with ERANGE.
 * Do not preload globally; only wpa_supplicant receives this shim.
 * NLA_SCAN_FLAGS=158 is stable UAPI. Omitting it requests legacy defaults.
 */
extern void *dlsym(void *, const char *);
typedef int (*put_fn)(void *, int, int, const void *);
int nla_put(void *message, int type, int length, const void *data)
{
    static put_fn original;
    if (type == 158 && length == 4)
        return 0;
    if (!original)
        original = (put_fn)dlsym((void *)-1, "nla_put");
    if (!original)
        return -1;
    return original(message, type, length, data);
}
