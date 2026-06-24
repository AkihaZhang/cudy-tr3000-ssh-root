#!/bin/sh
set -eu

cp -n /etc/init.d/dropbear /etc/init.d/dropbear.cudybak 2>/dev/null || true
sed -i '/bdinfo dbg.*return/d; /bdinfo.*dbg.*return/d' /etc/init.d/dropbear

uci set dropbear.@dropbear[0].enable='1'
uci set dropbear.@dropbear[0].PasswordAuth='on'
uci set dropbear.@dropbear[0].RootPasswordAuth='on'
uci set dropbear.@dropbear[0].RootLogin='on'
uci set dropbear.@dropbear[0].Port='22'
uci commit dropbear

uci set system.@system[0].ttylogin='1'
uci commit system

(echo password; sleep 1; echo password) | passwd root
/etc/init.d/dropbear enable

if [ -f /tmp/dropbear.manual.pid ]; then
	manual="$(cat /tmp/dropbear.manual.pid 2>/dev/null || true)"
	[ -n "$manual" ] && kill "$manual" 2>/dev/null || true
	rm -f /tmp/dropbear.manual.pid
fi

/etc/init.d/dropbear start

tmp_rc="$(mktemp)"
sed '/# CUDY_TR3000_SSH_BEGIN/,/# CUDY_TR3000_SSH_END/d' /etc/rc.local 2>/dev/null > "$tmp_rc" || true
sed -i '/^[[:space:]]*exit 0[[:space:]]*$/d' "$tmp_rc"
cat >> "$tmp_rc" <<'EOF'

# CUDY_TR3000_SSH_BEGIN
# Keep SSH alive after normal boot and after firmware upgrades that restore configs.
sed -i '/bdinfo dbg.*return/d; /bdinfo.*dbg.*return/d' /etc/init.d/dropbear 2>/dev/null || true
uci -q set dropbear.@dropbear[0].enable='1'
uci -q set dropbear.@dropbear[0].PasswordAuth='on'
uci -q set dropbear.@dropbear[0].RootPasswordAuth='on'
uci -q set dropbear.@dropbear[0].RootLogin='on'
uci -q set dropbear.@dropbear[0].Port='22'
uci -q commit dropbear
/etc/init.d/dropbear enable 2>/dev/null || true
/etc/init.d/dropbear running >/dev/null 2>&1 || /etc/init.d/dropbear start
# CUDY_TR3000_SSH_END

exit 0
EOF
cat "$tmp_rc" > /etc/rc.local
chmod +x /etc/rc.local
rm -f "$tmp_rc"

touch /etc/sysupgrade.conf
for keep in \
	/etc/sysupgrade.conf \
	/etc/rc.local \
	/etc/init.d/dropbear \
	/etc/config/dropbear \
	/etc/dropbear/ \
	/etc/passwd \
	/etc/shadow \
	/etc/rc.d/S19dropbear \
	/etc/rc.d/K50dropbear
do
	grep -qxF "$keep" /etc/sysupgrade.conf || echo "$keep" >> /etc/sysupgrade.conf
done

uci -q delete openvpn.client.server || true
uci -q delete openvpn.client.port || true
uci -q delete openvpn.client.file || true
uci -q delete openvpn.client.username || true
uci -q delete openvpn.client.password || true
uci set openvpn.client.enabled='0'
uci set openvpn.client.config='/etc/openvpn/client/client.ovpn'
uci commit openvpn

uci -q delete network.vpn.ifname || true
uci set network.vpn.proto='none'
uci commit network

rm -f /etc/openvpn/client/client.ovpn
rm -f /etc/openvpn/client/auth.txt
rm -f /etc/openvpn/client/askpass.txt

echo "persistent-ssh-ok"
