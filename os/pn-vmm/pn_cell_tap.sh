#!/bin/sh

set -eu

CHAIN_NAT="PN-CELL-NAT"
CHAIN_FWD="PN-CELL-FWD"

usage() {
    echo "usage: $0 up <tap-name> <subnet-index 0..255> [owner-user]" >&2
    echo "       $0 down <tap-name> <subnet-index>" >&2
    exit 2
}

[ "$#" -ge 3 ] || usage
CMD="$1"; TAP="$2"; IDX="$3"; OWNER="${4:-${SUDO_USER:-$(id -un)}}"

case "$TAP" in
    *[!a-zA-Z0-9_-]*|"") echo "bad tap name: $TAP (allowed: a-zA-Z0-9_-, 1..15 chars)" >&2; exit 2 ;;
esac
[ "${#TAP}" -le 15 ] || { echo "tap name too long: $TAP" >&2; exit 2; }
case "$IDX" in
    *[!0-9]*|"") echo "bad subnet index: $IDX" >&2; exit 2 ;;
esac
[ "$IDX" -le 255 ] || { echo "subnet index out of range: $IDX" >&2; exit 2; }

SUBNET="10.77.${IDX}.0/30"
HOST_IP="10.77.${IDX}.1"
GUEST_IP="10.77.${IDX}.2"

[ "$(id -u)" -eq 0 ] || { echo "$0: must run as root (sudo)" >&2; exit 1; }

ensure_rule() {
    t="$1"; c="$2"; shift 2
    iptables -t "$t" -C "$c" "$@" 2>/dev/null || iptables -t "$t" -A "$c" "$@"
}
drop_rule() {
    t="$1"; c="$2"; shift 2
    while iptables -t "$t" -C "$c" "$@" 2>/dev/null; do iptables -t "$t" -D "$c" "$@"; done
}

uplink() { ip -o -4 route show default 2>/dev/null | awk '{print $5; exit}'; }

case "$CMD" in
up)
    UPLINK="$(uplink)"
    [ -n "$UPLINK" ] || { echo "no default route — cannot pick a NAT uplink" >&2; exit 1; }

    ip link show "$TAP" >/dev/null 2>&1 || ip tuntap add mode tap user "$OWNER" name "$TAP"
    ip addr replace "${HOST_IP}/30" dev "$TAP"
    ip link set "$TAP" up

    if ! sudo -u "$OWNER" test -r /dev/net/tun || ! sudo -u "$OWNER" test -w /dev/net/tun; then
        if command -v setfacl >/dev/null 2>&1; then
            setfacl -m "u:${OWNER}:rw" /dev/net/tun
        else
            chmod 0666 /dev/net/tun
        fi
    fi

    [ "$(cat /proc/sys/net/ipv4/ip_forward)" = "1" ] || sysctl -qw net.ipv4.ip_forward=1

    iptables -t nat -N "$CHAIN_NAT" 2>/dev/null || true
    iptables -t filter -N "$CHAIN_FWD" 2>/dev/null || true
    iptables -t nat -C POSTROUTING -j "$CHAIN_NAT" 2>/dev/null || iptables -t nat -A POSTROUTING -j "$CHAIN_NAT"
    iptables -t filter -C FORWARD -j "$CHAIN_FWD" 2>/dev/null || iptables -t filter -I FORWARD 1 -j "$CHAIN_FWD"

    ensure_rule nat "$CHAIN_NAT" -s "$SUBNET" -o "$UPLINK" -j MASQUERADE
    ensure_rule filter "$CHAIN_FWD" -i "$TAP" -s "$SUBNET" -j ACCEPT
    ensure_rule filter "$CHAIN_FWD" -o "$TAP" -d "$SUBNET" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT

    echo "up: tap=$TAP owner=$OWNER host=${HOST_IP}/30 uplink=$UPLINK"
    echo "guest config: ip addr add ${GUEST_IP}/30 dev eth0; ip link set eth0 up; ip route add default via ${HOST_IP}"
    ;;
down)
    UPLINK="$(uplink)"
    if iptables -t nat -L "$CHAIN_NAT" >/dev/null 2>&1; then
        [ -n "$UPLINK" ] && drop_rule nat "$CHAIN_NAT" -s "$SUBNET" -o "$UPLINK" -j MASQUERADE
        iptables -t nat -L "$CHAIN_NAT" --line-numbers -n | awk -v s="10.77.${IDX}.0/30" '$0 ~ s {print $1}' \
            | sort -rn | while read -r ln; do iptables -t nat -D "$CHAIN_NAT" "$ln"; done
    fi
    if iptables -t filter -L "$CHAIN_FWD" >/dev/null 2>&1; then
        drop_rule filter "$CHAIN_FWD" -i "$TAP" -s "$SUBNET" -j ACCEPT
        drop_rule filter "$CHAIN_FWD" -o "$TAP" -d "$SUBNET" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
    fi

    if iptables -t nat -L "$CHAIN_NAT" -n 2>/dev/null | tail -n +3 | grep -qv '^$'; then :; else
        drop_rule nat POSTROUTING -j "$CHAIN_NAT"
        iptables -t nat -X "$CHAIN_NAT" 2>/dev/null || true
    fi
    if iptables -t filter -L "$CHAIN_FWD" -n 2>/dev/null | tail -n +3 | grep -qv '^$'; then :; else
        drop_rule filter FORWARD -j "$CHAIN_FWD"
        iptables -t filter -X "$CHAIN_FWD" 2>/dev/null || true
    fi

    ip link show "$TAP" >/dev/null 2>&1 && ip link del "$TAP"
    echo "down: tap=$TAP subnet=$SUBNET removed"
    ;;
*)
    usage
    ;;
esac
