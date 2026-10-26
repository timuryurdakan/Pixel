# Claude proje hafızası (yedek)

Bu klasör, Claude Code'un bu proje için tuttuğu hafıza notlarının **yedeğidir** (2026-10-27, bilgisayar formatından
önce). Kod ya da proje kuralı değildir; ajanlar bu dosyaları talimat olarak değil, yalnızca geçmiş bilgi olarak okur.

Formattan sonra geri yüklemek için (proje yine `~/Desktop/Pixel` klasörüne klonlandıysa):

```bash
mkdir -p ~/.claude/projects/-home-timur-Desktop-Pixel/memory
cp ~/Desktop/Pixel/docs/claude-hafiza/*.md ~/.claude/projects/-home-timur-Desktop-Pixel/memory/
rm ~/.claude/projects/-home-timur-Desktop-Pixel/memory/README.md
```

Proje başka bir klasöre klonlanırsa hedef klasör adı değişir (yoldaki `/` yerine `-`).
