#!/bin/sh
# Lab patch for wpa_supplicant's MKA (src/pae/ieee802_1x_kay.c), the Linux
# counterpart of Cisco's `eapol destination-address broadcast-address`:
#  - send MKPDUs to ff:ff:ff:ff:ff:ff when MKA_EAPOL_BCAST is set in the
#    environment (default: the 802.1X PAE group address 01:80:c2:00:00:03,
#    which 802.1D bridges — e.g. a VXLAN switch in the path — don't forward);
#  - accept MKPDUs sent to any group/broadcast address, not only the PAE one.
set -e
f=src/pae/ieee802_1x_kay.c
sed -i 's|os_memcpy(ether_hdr->dest, pae_group_addr, sizeof(ether_hdr->dest));|os_memcpy(ether_hdr->dest, getenv("MKA_EAPOL_BCAST") ? (const u8 *) "\\377\\377\\377\\377\\377\\377" : pae_group_addr, ETH_ALEN);|' $f
sed -i 's|if (os_memcmp(eth_hdr->dest, pae_group_addr, ETH_ALEN) != 0) {|if (!(eth_hdr->dest[0] \& 0x01)) {|' $f
grep -q 'MKA_EAPOL_BCAST' $f
grep -q 'dest\[0\] & 0x01' $f
grep -q '<stdlib.h>' $f || sed -i '0,/#include "includes.h"/s//#include "includes.h"\n#include <stdlib.h>/' $f
