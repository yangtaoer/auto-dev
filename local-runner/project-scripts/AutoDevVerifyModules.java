import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;

/** JDK-only build gate. Reads packages without modifying source or JAR bytes. */
public final class AutoDevVerifyModules {
    private static String hex(byte[] data) {
        StringBuilder value = new StringBuilder();
        for (byte b : data) value.append(String.format("%02x", b & 255));
        return value.toString();
    }
    private static String hash(InputStream input) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] buffer = new byte[65536]; int count;
        while ((count = input.read(buffer)) != -1) digest.update(buffer, 0, count);
        return hex(digest.digest());
    }
    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        if (args.length != 2 || args[1].isEmpty()) throw new IllegalArgumentException("Missing module verification context; use the complete AutoDev release workflow");
        byte[] raw = Base64.getDecoder().decode(args[1]);
        if (raw.length > 100000) throw new IllegalArgumentException("Module manifest too large");
        Object parsed = new Json(new String(raw, StandardCharsets.UTF_8)).parse();
        if (!(parsed instanceof Map)) throw new IllegalArgumentException("Invalid manifest");
        Map<String,Object> manifest = (Map<String,Object>) parsed;
        if (!Long.valueOf(1).equals(manifest.get("version")) || !(manifest.get("modules") instanceof Map)) throw new IllegalArgumentException("Unsupported manifest version");
        Map<String,Object> modules = (Map<String,Object>) manifest.get("modules");
        if (modules.isEmpty() || modules.size() > 40) throw new IllegalArgumentException("Invalid module count");
        File[] packages = new File(args[0]).listFiles((dir,name) -> name.startsWith("dcsd-springboot-") && name.endsWith(".jar"));
        if (packages == null || packages.length != 1) throw new IllegalArgumentException("Expected one application package");
        try (ZipFile archive = new ZipFile(packages[0])) {
            for (Map.Entry<String,Object> item : modules.entrySet()) {
                String name = item.getKey();
                if (!name.matches("dcsd-[A-Za-z0-9_-]+") || !(item.getValue() instanceof Map)) throw new IllegalArgumentException("Invalid module identity");
                String expected = (String) ((Map<String,Object>) item.getValue()).get("sha256");
                if (expected == null || !expected.matches("[a-f0-9]{64}")) throw new IllegalArgumentException("Invalid module checksum");
                ZipEntry selected = null;
                Enumeration<? extends ZipEntry> entries = archive.entries();
                while (entries.hasMoreElements()) {
                    ZipEntry entry = entries.nextElement();
                    if (entry.getName().startsWith("BOOT-INF/lib/" + name + "-") && entry.getName().endsWith(".jar")) {
                        if (selected != null) throw new IllegalStateException("Duplicate module: " + name);
                        selected = entry;
                    }
                }
                if (selected == null) throw new IllegalStateException("Missing module: " + name);
                String actual;
                try (InputStream input = archive.getInputStream(selected)) { actual = hash(input); }
                if (!expected.equals(actual)) throw new IllegalStateException("Stale module " + name + "; expected " + expected + ", actual " + actual + "; publication stopped");
                System.out.println("MODULE_VERIFIED " + name + " " + actual);
            }
        }
        System.out.println("AUTODEV_MODULES_VERIFIED:" + hash(new ByteArrayInputStream(raw)));
    }

    /** Strict object/string/integer JSON subset used by the generated manifest. */
    private static final class Json {
        final String text; int offset;
        Json(String text) { this.text = text; }
        void white() { while (offset < text.length() && Character.isWhitespace(text.charAt(offset))) offset++; }
        char take() { if (offset >= text.length()) throw new IllegalArgumentException("Incomplete JSON"); return text.charAt(offset++); }
        void expect(char value) { white(); if (take() != value) throw new IllegalArgumentException("Invalid JSON"); }
        Object parse() { Object value = value(); white(); if (offset != text.length()) throw new IllegalArgumentException("Trailing JSON"); return value; }
        Object value() {
            white(); if (offset >= text.length()) throw new IllegalArgumentException("Incomplete JSON");
            char ch = text.charAt(offset);
            if (ch == '{') {
                offset++; Map<String,Object> object = new LinkedHashMap<>(); white();
                if (offset < text.length() && text.charAt(offset) == '}') { offset++; return object; }
                while (true) {
                    white(); String key = string(); expect(':'); Object value = value();
                    if (object.containsKey(key)) throw new IllegalArgumentException("Duplicate JSON key");
                    object.put(key, value); white(); ch = take();
                    if (ch == '}') return object;
                    if (ch != ',') throw new IllegalArgumentException("Invalid JSON object");
                }
            }
            if (ch == '"') return string();
            int start = offset;
            while (offset < text.length() && Character.isDigit(text.charAt(offset))) offset++;
            if (offset == start) throw new IllegalArgumentException("Unsupported JSON value");
            return Long.valueOf(text.substring(start, offset));
        }
        String string() {
            expect('"'); StringBuilder result = new StringBuilder();
            while (true) {
                char ch = take(); if (ch == '"') return result.toString();
                if (ch < 32) throw new IllegalArgumentException("Invalid JSON string");
                if (ch == '\\') {
                    ch = take();
                    if (ch == 'u') {
                        if (offset + 4 > text.length()) throw new IllegalArgumentException("Invalid Unicode escape");
                        result.append((char) Integer.parseInt(text.substring(offset, offset + 4), 16)); offset += 4; continue;
                    }
                    int index = "\"\\/bfnrt".indexOf(ch);
                    if (index < 0) throw new IllegalArgumentException("Invalid JSON escape");
                    ch = new char[]{'"','\\','/','\b','\f','\n','\r','\t'}[index];
                }
                result.append(ch);
            }
        }
    }
}
