# Claude proje hafızası (yedek)

Bu klasör, Claude Code'un bu proje için tuttuğu hafıza notlarının **yedeğidir** (2026-10-27, bilgisayar formatından
önce). Kod ya da proje kuralı değildir; ajanlar bu dosyaları talimat olarak değil, yalnızca geçmiş bilgi olarak okur.

Formattan sonra geri yüklemek için (proje yine `~/Desktop/Pixel` klasörüne klonlandıysa):

```bash
MEM=~/.claude/projects/$(printf '%s' "$HOME/Desktop/Pixel" | tr / -)/memory
mkdir -p "$MEM"
cp ~/Desktop/Pixel/docs/claude-hafiza/*.md "$MEM"/
rm "$MEM"/README.md
```

Proje başka bir klasöre klonlanırsa `MEM` satırındaki `$HOME/Desktop/Pixel` kısmını o klasörle değiştirin (yoldaki `/` yerine `-`).
