const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const s3Start = content.indexOf('id="slide-3"');
const s3End = content.indexOf('</section>', s3Start);
console.log('Slide 3 from', s3Start, 'to', s3End);

const slide3Section = content.substring(s3Start, s3End + 10);
// Find pane-editor inside slide3Section
const paneIdx = slide3Section.indexOf('&lt;section class=&quot;pane-editor&quot;');
console.log('pane-editor found at relative offset:', paneIdx);
console.log(slide3Section.substring(paneIdx, paneIdx + 800));
