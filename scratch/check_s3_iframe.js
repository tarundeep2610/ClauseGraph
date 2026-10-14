const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const s3Match = content.match(/id=["']slide-3["'][\s\S]*?<\/section>/i);
if (s3Match) {
  console.log('Slide 3 section length:', s3Match[0].length);
  const iframeMatch = s3Match[0].match(/<iframe srcdoc="([\s\S]*?)"><\/iframe>/i);
  if (iframeMatch) {
    console.log('Slide 3 iframe srcdoc length:', iframeMatch[1].length);
  } else {
    console.log('iframe srcdoc not matched!');
  }
}
