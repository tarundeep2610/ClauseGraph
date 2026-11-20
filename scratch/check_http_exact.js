const http = require('http');

http.get('http://localhost:4891/', (res) => {
  let body = '';
  res.on('data', chunk => body += chunk);
  res.on('end', () => {
    const s3Idx = body.indexOf('id="slide-3"');
    const s3Next = body.indexOf('</section>', s3Idx);
    const s3Html = body.substring(s3Idx, s3Next);
    console.log('HTTP Server Slide 3 Scan Header:');
    const scanMatch = s3Html.match(/Original Scan[^<]*/g);
    console.log('Scan matches in HTTP response:', scanMatch);
    const editorMatch = s3Html.match(/Extracted Text[^<]*/g);
    console.log('Editor matches in HTTP response:', editorMatch);
  });
});
