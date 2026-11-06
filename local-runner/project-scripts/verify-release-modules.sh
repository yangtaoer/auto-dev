set -eu
workdir=$(mktemp -d "${AGENT_TEMPDIRECTORY:-/tmp}/autodev-gate.XXXXXX")
cleanup() {
    rm -f "$workdir/AutoDevVerifyModules.java" "$workdir/AutoDevVerifyModules.class" "$workdir/AutoDevVerifyModules\$Json.class"
    rmdir "$workdir" || true
}
trap cleanup EXIT
cat > "$workdir/AutoDevVerifyModules.java" <<'AUTODEV_JAVA'
@@JAVA_SOURCE@@
AUTODEV_JAVA
javac_bin="${JAVA_HOME:-}/bin/javac"
java_bin="${JAVA_HOME:-}/bin/java"
if [ ! -x "$javac_bin" ]; then javac_bin=$(command -v javac); fi
if [ ! -x "$java_bin" ]; then java_bin=$(command -v java); fi
"$javac_bin" -encoding UTF-8 -source 8 -target 8 -Xlint:-options -d "$workdir" "$workdir/AutoDevVerifyModules.java"
"$java_bin" -cp "$workdir" AutoDevVerifyModules "${BUILD_SOURCESDIRECTORY:?Missing source directory}/target" "${AUTODEV_EXPECTED_MODULES:?Missing module context}"
