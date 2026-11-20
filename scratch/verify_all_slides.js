const fs = require('fs');

const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

for (let s = 1; s <= 8; s++) {
  const slideRegex = new RegExp(`id=["']slide-${s}["'][\\s\\S]*?<iframe[^>]*srcdoc="([\\s\\S]*?)"[\\s\\S]*?<\/iframe>`, 'i');
  const m = content.match(slideRegex);
  if (!m) {
    console.log(`Slide ${s}: iframe with srcdoc not matched!`);
  } else {
    const srcdoc = m[1];
    // Check if there are unescaped double quotes inside srcdoc
    // In srcdoc="...", any literal unescaped double quote breaks the attribute unless it's escaped or HTML entity &quot;
    // Let's test if srcdoc contains unescaped "
    const unescapedDoubleQuotes = (srcdoc.match(/(?<!\\)"/g) || []).length;
    console.log(`Slide ${s}: matched srcdoc length ${srcdoc.length}, unescaped double quotes: ${unescapedDoubleQuotes}`);
  }
}
