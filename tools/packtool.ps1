param([string]$Pack, [string]$Filter = '\.lua$', [string]$OutDir = '')
# Minimal reader for Total War: Shogun 2 pack files.
#   PFH3 (original): 32-byte header, entries [u32 size][name\0], data contiguous after the index.
#   PFHs (Complete Edition): 32-byte header, entries [u64 offset][u32 size][u32 stored][name\0]; stored < size = raw LZ4 block.
Add-Type -TypeDefinition @"
public static class Lz4 {
  public static byte[] Decode(byte[] src, int outLen) {
    byte[] dst = new byte[outLen]; int s = 0, d = 0;
    while (s < src.Length) {
      int token = src[s++]; int lit = token >> 4;
      if (lit == 15) { int b; do { b = src[s++]; lit += b; } while (b == 255); }
      System.Array.Copy(src, s, dst, d, lit); s += lit; d += lit;
      if (s >= src.Length) break;
      int off = src[s] | (src[s + 1] << 8); s += 2;
      int ml = token & 15;
      if (ml == 15) { int b; do { b = src[s++]; ml += b; } while (b == 255); }
      ml += 4; int m = d - off;
      for (int i = 0; i < ml; i++) dst[d++] = dst[m + i];
    }
    return dst;
  }
}
"@ -ErrorAction SilentlyContinue
$fs = [IO.File]::OpenRead($Pack); $br = New-Object IO.BinaryReader($fs)
$magic = [Text.Encoding]::ASCII.GetString($br.ReadBytes(4))
$type = $br.ReadUInt32(); $depCount = $br.ReadUInt32(); $depSize = $br.ReadUInt32()
$fileCount = $br.ReadUInt32(); $indexSize = $br.ReadUInt32(); $br.ReadBytes(8) | Out-Null
$fs.Position += $depSize
$entries = New-Object System.Collections.Generic.List[object]
for ($i = 0; $i -lt $fileCount; $i++) {
  $off = -1; $stored = 0
  if ($magic -eq 'PFHs') { $off = $br.ReadInt64(); $size = $br.ReadUInt32(); $stored = $br.ReadUInt32() }
  else { $size = $br.ReadUInt32(); $stored = $size; if ($magic -eq 'PFH4' -and ($type -band 0x40)) { $br.ReadUInt32() | Out-Null } }
  $sb = New-Object Text.StringBuilder; while (($c = $br.ReadByte()) -ne 0) { [void]$sb.Append([char]$c) }
  $entries.Add([pscustomobject]@{ Name = $sb.ToString(); Size = $size; Stored = $stored; Offset = $off })
}
$dataPos = $fs.Position
foreach ($e in $entries) {
  if ($e.Offset -lt 0) { $e.Offset = $dataPos; $dataPos += $e.Stored }
}
Write-Host "magic=$magic type=0x$('{0:x}' -f $type) files=$fileCount"
foreach ($e in $entries) {
  if ($e.Name -match $Filter) {
    "{0,9} {1,9}  {2}" -f $e.Size, $e.Stored, $e.Name
    if ($OutDir) {
      $fs.Position = $e.Offset; $raw = $br.ReadBytes($e.Stored)
      if ($e.Stored -lt $e.Size) { $raw = [Lz4]::Decode($raw, $e.Size) }
      $p = Join-Path $OutDir $e.Name; New-Item -ItemType Directory -Force (Split-Path $p) | Out-Null
      [IO.File]::WriteAllBytes($p, $raw)
    }
  }
}
$fs.Close()
