#!/usr/bin/env python3

import os

BUSYBOX = "/usr/bin/busybox"
OUT = os.environ.get("OUT", "kernel/initramfs-cell.cpio")

S_IFDIR = 0o040000
S_IFREG = 0o100000
S_IFLNK = 0o120000
S_IFCHR = 0o020000

INIT = b"""#!/bin/busybox sh
export PATH=/bin
busybox mkdir -p /proc /sys /dev /lower /delta /newroot
busybox mount -t proc none /proc
busybox mount -t sysfs none /sys
busybox mount -t devtmpfs none /dev 2>/dev/null
busybox --install -s /bin 2>/dev/null
echo ""
echo "PN_VMM_CELL_INITRAMFS_ALIVE"
busybox mount -o ro -t ext4 /dev/vda /lower && echo "PN_MOUNT_BASE_OK" || echo "PN_MOUNT_BASE_FAIL"
busybox mount -t ext4 /dev/vdb /delta && echo "PN_MOUNT_DELTA_OK" || echo "PN_MOUNT_DELTA_FAIL"
busybox mkdir -p /delta/upper /delta/work
if busybox mount -t overlay overlay -o lowerdir=/lower,upperdir=/delta/upper,workdir=/delta/work /newroot 2>/dev/null; then
  echo "PN_OVERLAY_OK"
else
  echo "PN_OVERLAY_UNAVAILABLE_FALLBACK_BIND"
  busybox mount --bind /lower /newroot
fi
# ---------------------------------------------------------------- usr-merge
# Ubuntu 24.04 ist merged-/usr-only. glibcs preinst macht die Probe
#     dpkg-divert --divert "/lib64.usr-is-merged" "/lib64"
# und erwartet dabei einen SYMLINK. In unserem handkopierten Baum ist /lib64 ein echtes
# Verzeichnis -> "dpkg-divert: error: cannot divert directories" -> preinst exit 2 -> glibc
# bricht ab. Bei ehrlicher Paketdatenbank ist glibc der ALLERERSTE Einbau, also scheitert
# danach jede Installation. Gemessen 12.08.2026 in der Marketing-Zelle.
#
# Das MUSS hier passieren, VOR switch_root. Zur Laufzeit ist es zu spaet: dort haengen
# bereits Kisten-Symlinks in /usr/bin, die die echten Programme aus /bin verdecken --
# nachgemessen ergab das "Too many levels of symbolic links", die Zelle war nicht mehr
# bedienbar. Hier ist der Baum noch unberuehrt.
#
# Idempotent ueber die Symlink-Pruefung; schlaegt etwas fehl, bleibt der Baum wie er war.
# ACHTUNG: Die echte Shell aus /bin MUSS vor der Verschmelzung hinueber.
# Der Einrichter legt dash und bash als BINAERDATEIEN in /bin ab; in /usr/bin liegt
# derweil der Kisten-Symlink gleichen Namens. `cp -a` unten laesst das vorhandene Ziel
# stehen (richtig so, sonst gewaennen die BusyBox-Applets gegen die echten Programme der
# Kiste) -- und `rm -rf /newroot/bin` wirft das Original danach weg. Gemessen 12.08.2026:
# danach blieb nur der Rueckweg auf BusyBox, und damit waere die Applet-Verdeckung zurueck,
# die uns diesen ganzen Tag gekostet hat.
# Nur wenn /bin noch ein echtes Verzeichnis ist -- ist es bereits ein Symlink, waeren
# Quelle und Ziel dieselbe Datei, und das Entfernen loeschte die Shell.
if [ -d /newroot/bin ] && [ ! -L /newroot/bin ]; then
  for s in sh dash bash; do
    q=/newroot/bin/$s
    t=/newroot/usr/bin/$s
    [ -e "$q" ] || continue
    [ -L "$t" ] || continue
    case "$(busybox readlink "$t")" in
      /opt/kits/*) ;;
      *) continue ;;
    esac
    if [ -L "$q" ]; then
      case "$(busybox readlink "$q")" in
        /opt/kits/*) continue ;;
      esac
    fi
    busybox rm -f "$t" && busybox cp -a "$q" "$t" \
      && echo "PN_SHELL_ECHT $s (aus /bin gerettet)" \
      || echo "PN_SHELL_FAIL $s"
  done
fi

for p in bin sbin lib lib64; do
  if [ -d /newroot/$p ] && [ ! -L /newroot/$p ]; then
    busybox mkdir -p /newroot/usr/$p
    busybox cp -a /newroot/$p/. /newroot/usr/$p/ 2>/dev/null
    # Erst ZAEHLEN, dann entfernen. Ein halb kopiertes /bin ist eine tote Zelle: die alten
    # Dateien waeren weg, die neuen nicht da, und nichts in der Zelle liesse sich mehr
    # starten. Lieber unverschmolzen weiterlaufen als unbedienbar.
    a=$(busybox ls -a /newroot/$p | busybox wc -l)
    b=$(busybox ls -a /newroot/usr/$p | busybox wc -l)
    echo "PN_USRMERGE_ZAEHLT $p vorher=$a nachher=$b"
    if [ "$b" -ge "$a" ]; then
      busybox rm -rf /newroot/$p && busybox ln -s usr/$p /newroot/$p \
        && echo "PN_USRMERGE_OK $p" || echo "PN_USRMERGE_FAIL $p"
    else
      echo "PN_USRMERGE_ABBRUCH $p (Kopie unvollstaendig, Baum bleibt wie er war)"
    fi
  else
    echo "PN_USRMERGE_SKIP $p"
  fi
done

# ------------------------------------------------- Shell-Kette nach der Verschmelzung
# ACHTUNG: Nach der Verschmelzung sind /bin und /usr/bin DASSELBE Verzeichnis. Damit trifft die
# echte Shell aus /bin auf den Kisten-Symlink in /usr/bin -- und `cp -a` laesst das
# vorhandene Ziel stehen. Das ist oben richtig so: wuerde es ueberschreiben, gewaennen die
# BusyBox-Applets aus /bin gegen die 334 echten Programme der Kiste. Uebrig bleibt aber
# /usr/bin/dash -> /opt/kits/dpkg/bin/dash, und dieser Wrapper ist ein Skript mit
# "#!/bin/sh". Also: /bin/sh -> usr/bin/sh -> dash -> Wrapper -> "#!/bin/sh" -> von vorn.
# Der Kern bricht nach vier Runden mit ELOOP ab -- die Zelle hat dann KEINE Shell mehr.
#
# Vor der Verschmelzung war das harmlos: /bin/dash (echt) und /usr/bin/dash (Wrapper) waren
# zwei verschiedene Dateien. Die Zaehlpruefung oben kann es nicht sehen, denn /usr/bin hat
# ohnehin mehr Eintraege als /bin.
#
# Gemessen 12.08.2026 an einer Kopie des Marketing-Deltas: die Sitzbahn endete sofort nach
# dem Start (PN_CELL_SEAT_ENDED), weil ihr Starter ueber /bin/sh laeuft.
#
# Repariert wird mit dem einzigen Material, das hier nachweislich echt ist: der Basis unter
# /lower. Sie ist schreibgeschuetzt und traegt die unveraenderten Originale. Und es muss
# HIER geschehen -- der Einrichter in der Zelle koennte es nicht, er braucht selbst eine
# funktionierende /bin/sh, um ueberhaupt zu starten.
for s in sh dash bash; do
  t=/newroot/usr/bin/$s
  [ -L "$t" ] || continue
  case "$(busybox readlink "$t")" in
    /opt/kits/*) ;;
    *) continue ;;
  esac
  if [ -e /lower/usr/bin/$s ]; then
    busybox rm -f "$t" && busybox cp -a /lower/usr/bin/$s "$t" \
      && echo "PN_SHELL_ECHT $s (aus der Basis zurueckgeholt)" \
      || echo "PN_SHELL_FAIL $s"
  elif [ -e /newroot/usr/bin/busybox ]; then
    busybox rm -f "$t" && busybox ln -s busybox "$t" \
      && echo "PN_SHELL_BUSYBOX $s (Rueckweg, kein Original in der Basis)" \
      || echo "PN_SHELL_FAIL $s"
  else
    echo "PN_SHELL_FAIL $s (weder Basis noch busybox vorhanden)"
  fi
done

# ------------------------------------------------- Der Init der Zelle gehoert der Zelle
# switch_root startet /sbin/init. Nach der usr-Verschmelzung ist /sbin ein Symlink auf
# usr/sbin -- und dort legt das Paket systemd-sysv seinen eigenen init an. Ein einziges
# `apt install`, das systemd nachzieht, tauscht damit das PID 1 der Zelle aus.
#
# Gemessen 13.08.2026 an der Marketing-Zelle: /usr/sbin/init zeigte auf
# ../lib/systemd/systemd (angelegt 12.08. um 21:07 waehrend einer Paketinstallation).
# Beim naechsten Start bootete systemd statt der Zelle: der Gast kam bis graphical.target,
# aber vsock-seat lief nie -- der Seat schickte GAR NICHTS, und das Portal riss die VM
# nach jedem Versuch ab. Die Sitzung war unbenutzbar, ohne dass irgendwo "kaputt" stand.
#
# Die Basis unter /lower ist schreibgeschuetzt und traegt den echten Init. Sie ist hier die
# einzige Quelle, der man trauen kann -- und es muss HIER geschehen, denn danach ist das
# falsche PID 1 bereits gestartet.
if [ -L /newroot/sbin/init ]; then
  case "$(busybox readlink /newroot/sbin/init)" in
    *systemd*)
      if [ -f /lower/sbin/init ] && [ ! -L /lower/sbin/init ]; then
        busybox rm -f /newroot/sbin/init \\
          && busybox cp -a /lower/sbin/init /newroot/sbin/init \\
          && echo "PN_INIT_ZURUECKGEHOLT (ein Paket hatte /sbin/init auf systemd gelegt)" \\
          || echo "PN_INIT_FAIL"
      else
        echo "PN_INIT_WARNUNG systemd auf /sbin/init, kein Original in der Basis"
      fi
      ;;
  esac
fi

echo "PN_CELL_LS_ROOT_BEGIN"; busybox ls -1 /newroot; echo "PN_CELL_LS_ROOT_END"
echo -n "PN_CELL_HOSTHOME="; leak=0; for d in /newroot/home/*; do [ "$d" = "/newroot/home/owner" ] && continue; [ -e "$d" ] && leak=1; done; if [ "$leak" = 1 ]; then echo "VISIBLE_BAD"; else echo "ABSENT_GOOD"; fi
echo -n "PN_CELL_OWNER_DATA="; busybox cat /newroot/home/owner/HELLO.txt 2>/dev/null; echo ""
if [ -f /newroot/home/owner/PERSIST ]; then
  echo -n "PN_CELL_PERSIST_FOUND="; busybox cat /newroot/home/owner/PERSIST; echo ""
else
  echo "written-boot1" > /newroot/home/owner/PERSIST 2>/dev/null && echo "PN_CELL_PERSIST_WRITTEN" || echo "PN_CELL_PERSIST_WRITE_FAIL"
fi
busybox sync
echo "PN_CELL_READY"
# Hand the cell its own root. switch_root MUST be PID 1 (busybox bb_show_usage()s otherwise), so we
# exec it (no fork). switch_root itself verifies /newroot is a separate mount; we just confirm the
# cell init is present+executable (busybox has no `mountpoint` applet on this build).
if [ -x /newroot/sbin/init ]; then
  echo "PN_SWITCHROOT_ATTEMPT"
  exec busybox switch_root /newroot /sbin/init
fi
echo "PN_SWITCHROOT_SKIP_NO_INIT"
exec busybox sh
"""

class Cpio:
    def __init__(self):
        self.buf = bytearray()
        self.ino = 721

    def _pad4(self):
        while len(self.buf) % 4:
            self.buf += b"\x00"

    def add(self, name, mode, data=b"", rdevmajor=0, rdevminor=0, nlink=1):
        name_b = name.encode() + b"\x00"
        fields = [self.ino, mode, 0, 0, nlink, 0, len(data), 0, 0, rdevmajor, rdevminor, len(name_b), 0]
        self.ino += 1
        hdr = b"070701" + b"".join(b"%08X" % (f & 0xFFFFFFFF) for f in fields)
        self.buf += hdr + name_b
        self._pad4()
        self.buf += data
        self._pad4()

    def finish(self):
        self.add("TRAILER!!!", 0, nlink=1)
        return bytes(self.buf)

def main():
    with open(BUSYBOX, "rb") as f:
        bb = f.read()
    c = Cpio()
    for d in ["bin", "dev", "proc", "sys", "lower", "delta", "newroot", "tmp"]:
        c.add(d, S_IFDIR | 0o755, nlink=2)
    c.add("bin/busybox", S_IFREG | 0o755, bb)
    c.add("init", S_IFREG | 0o755, INIT)
    c.add("bin/sh", S_IFLNK | 0o777, b"busybox")
    c.add("dev/console", S_IFCHR | 0o600, rdevmajor=5, rdevminor=1)
    c.add("dev/null", S_IFCHR | 0o666, rdevmajor=1, rdevminor=3)
    c.add("dev/tty", S_IFCHR | 0o666, rdevmajor=5, rdevminor=0)
    c.add("dev/ttyS0", S_IFCHR | 0o660, rdevmajor=4, rdevminor=64)
    data = c.finish()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(data)
    print("wrote %s (%d bytes)" % (OUT, len(data)))

if __name__ == "__main__":
    main()
