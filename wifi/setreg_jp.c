/* Minimal userspace regulatory responder for the pre-regulatory.db kernel.
 * Conservative subset of wireless-regdb's JP rules (db.txt saved alongside).
 * 20 dBm max; 5 GHz remains passive/no-IR and DFS rules retain DFS.
 * Loaded once with the installed libnl libraries; not an application preload.
 */
#include <linux/nl80211.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
struct nl_sock; struct nl_msg; struct nlattr;
extern struct nl_sock *nl_socket_alloc(void);
extern int genl_connect(struct nl_sock *);
extern int genl_ctrl_resolve(struct nl_sock *, const char *);
extern struct nl_msg *nlmsg_alloc(void);
extern void *genlmsg_put(struct nl_msg *, unsigned, unsigned, int, int, int, unsigned char, unsigned char);
extern int nla_put(struct nl_msg *, int, int, const void *);
extern int nla_put_u32(struct nl_msg *, int, unsigned);
extern int nla_put_u8(struct nl_msg *, int, unsigned char);
extern struct nlattr *nla_nest_start(struct nl_msg *, int);
extern int nla_nest_end(struct nl_msg *, struct nlattr *);
extern int nl_send_auto_complete(struct nl_sock *, struct nl_msg *);
extern int nl_wait_for_ack(struct nl_sock *);
extern const char *nl_geterror(int);
static void rule(struct nl_msg *m, int index, unsigned start, unsigned end, unsigned bw, unsigned flags)
{
 struct nlattr *n = nla_nest_start(m, index);
 if (!n || nla_put_u32(m, NL80211_ATTR_REG_RULE_FLAGS, flags) ||
     nla_put_u32(m, NL80211_ATTR_FREQ_RANGE_START, start*1000) ||
     nla_put_u32(m, NL80211_ATTR_FREQ_RANGE_END, end*1000) ||
     nla_put_u32(m, NL80211_ATTR_FREQ_RANGE_MAX_BW, bw*1000) ||
     nla_put_u32(m, NL80211_ATTR_POWER_RULE_MAX_ANT_GAIN, 0) ||
     nla_put_u32(m, NL80211_ATTR_POWER_RULE_MAX_EIRP, 2000)) exit(2);
 nla_nest_end(m,n);
}
__attribute__((constructor)) static void apply(void)
{
 struct nl_sock *s=nl_socket_alloc(); struct nl_msg *m=nlmsg_alloc();
 if (!s || !m || genl_connect(s)) exit(2);
 int id=genl_ctrl_resolve(s,"nl80211"); if(id<0) exit(2);
 const char *country=getenv("JTB1_REG_COUNTRY");
 if(!country) country="JP";
 if(strcmp(country,"JP") && strcmp(country,"00")) exit(2);
 if (!genlmsg_put(m,0,0,id,0,0,NL80211_CMD_SET_REG,0) ||
     nla_put(m, NL80211_ATTR_REG_ALPHA2,2,country)) exit(2);
 if(!strcmp(country,"JP") && nla_put_u8(m,NL80211_ATTR_DFS_REGION,3)) exit(2);
 struct nlattr *rules=nla_nest_start(m,NL80211_ATTR_REG_RULES); if(!rules) exit(2);
 if(!strcmp(country,"00")) {
  /* Fulfil initial pending world request without widening its domain. */
  rule(m,1,2402,2472,40,0);
  rule(m,2,2457,2482,20,(1U<<7)|(1U<<8));
 } else {
  rule(m,1,2402,2482,40,0);
  rule(m,2,5170,5250,40,(1U<<7)|(1U<<8));
  rule(m,3,5250,5330,40,(1U<<7)|(1U<<8)|(1U<<4));
  rule(m,4,5490,5710,40,(1U<<7)|(1U<<8)|(1U<<4));
 }
 nla_nest_end(m,rules);
 int r=nl_send_auto_complete(s,m); if(r>=0) r=nl_wait_for_ack(s);
 printf("%s conservative regulatory response: %d (%s)\n",country,r,r<0?nl_geterror(r):"OK");
 if(r<0) exit(1);
}
