const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const s3Start = content.indexOf('id="slide-3"');
const s3End = content.indexOf('</section>', s3Start);
const slide3Section = content.substring(s3Start, s3End + 10);
const paneIdx = slide3Section.indexOf('&lt;section class=&quot;pane-editor&quot;');
const endSectionIdx = slide3Section.indexOf('&lt;/section&gt;', paneIdx);
console.log(slide3Section.substring(paneIdx, endSectionIdx + 15));
