const fs = require('fs');

const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

// Extract Slide 2 iframe srcdoc:
const m2 = content.match(/id=["']slide-2["'][\s\S]*?<iframe[^>]*srcdoc="([\s\S]*?)"[\s\S]*?<\/iframe>/i);
if (!m2) {
  console.error('Slide 2 srcdoc not found!');
  process.exit(1);
}

let s2Srcdoc = m2[1];

// In Slide 2, modify it to become Slide 3:
// 1. Change title:
s2Srcdoc = s2Srcdoc.replace('&lt;title&gt;02 Clean Document Studio&lt;/title&gt;', '&lt;title&gt;03 Guided Review Queue&lt;/title&gt;');

// 2. In task-rail, mark Review button active and Outline inactive:
// In Slide 2:
// <div class="rail-btn-wrap" title="Quality Review Flags (Ctrl+2)" onclick="typeof toggleQualityFlyout === 'function' ? toggleQualityFlyout() : window.parent.goToSlideById('slide-3')">
//   <button class="rail-icon-btn">⚠️<span class="rail-badge-alert">3</span></button>
//   <span class="rail-btn-label">Review</span>
// </div>

const s2RailReview = `      &lt;div class=&quot;rail-btn-wrap&quot; title=&quot;Quality Review Flags (Ctrl+2)&quot; onclick=&quot;typeof toggleQualityFlyout === &#x27;function&#x27; ? toggleQualityFlyout() : window.parent.goToSlideById(&#x27;slide-3&#x27;)&quot;&gt;
        &lt;button class=&quot;rail-icon-btn&quot;&gt;⚠️&lt;span class=&quot;rail-badge-alert&quot;&gt;3&lt;/span&gt;&lt;/button&gt;
        &lt;span class=&quot;rail-btn-label&quot;&gt;Review&lt;/span&gt;
      &lt;/div&gt;

      &lt;!-- Quality Flyout --&gt;
      &lt;div id=&quot;qualityFlyout&quot; class=&quot;quality-rail-flyout&quot;&gt;
        &lt;div class=&quot;flyout-header&quot;&gt;
          &lt;span&gt;⚠️ 3 Flagged Pages&lt;/span&gt;
          &lt;span style=&quot;font-size:10px; color:var(--text-subtle);&quot;&gt;OCR Confidence&lt;/span&gt;
        &lt;/div&gt;
        &lt;div class=&quot;flyout-item&quot; onclick=&quot;window.parent.goToSlideById(&#x27;slide-3&#x27;)&quot;&gt;
          &lt;span&gt;Page 14&lt;/span&gt;
          &lt;span class=&quot;score-pill-warn&quot;&gt;71% Low Conf&lt;/span&gt;
        &lt;/div&gt;
        &lt;div class=&quot;flyout-item&quot; onclick=&quot;window.parent.goToSlideById(&#x27;slide-3&#x27;)&quot;&gt;
          &lt;span&gt;Page 22&lt;/span&gt;
          &lt;span class=&quot;score-pill-warn&quot;&gt;78% Low Conf&lt;/span&gt;
        &lt;/div&gt;
        &lt;div class=&quot;flyout-item&quot; onclick=&quot;window.parent.goToSlideById(&#x27;slide-3&#x27;)&quot;&gt;
          &lt;span&gt;Page 39&lt;/span&gt;
          &lt;span class=&quot;score-pill-warn&quot;&gt;82% Blurred Seal&lt;/span&gt;
        &lt;/div&gt;
        &lt;button class=&quot;btn-flyout-wizard&quot; onclick=&quot;window.parent.goToSlideById(&#x27;slide-3&#x27;)&quot;&gt;Open Review Wizard ➔&lt;/button&gt;
      &lt;/div&gt;`;

const s3ReviewPanel = `      &lt;div class=&quot;rail-btn-wrap active&quot; title=&quot;Quality Review Flags (Ctrl+2)&quot; onclick=&quot;window.parent.goToSlideById(&#x27;slide-2&#x27;)&quot;&gt;
        &lt;button class=&quot;rail-icon-btn active&quot;&gt;⚠️&lt;span class=&quot;rail-badge-alert&quot;&gt;3&lt;/span&gt;&lt;/button&gt;
        &lt;span class=&quot;rail-btn-label&quot; style=&quot;color:var(--primary); font-weight:700;&quot;&gt;Review&lt;/span&gt;
      &lt;/div&gt;
    &lt;/aside&gt;

    &lt;!-- Opened Review Queue Section --&gt;
    &lt;aside style=&quot;width:240px; background:var(--bg-surface); border-right:1px solid var(--border); display:flex; flex-direction:column; flex-shrink:0;&quot;&gt;
      &lt;div style=&quot;padding:12px 14px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center;&quot;&gt;
        &lt;div&gt;
          &lt;div style=&quot;font-size:12px; font-weight:800; color:var(--text-main);&quot;&gt;Review Queue&lt;/div&gt;
          &lt;div style=&quot;font-size:11px; color:var(--text-subtle);&quot;&gt;3 of 42 pages flagged&lt;/div&gt;
        &lt;/div&gt;
        &lt;button onclick=&quot;window.parent.goToSlideById(&#x27;slide-2&#x27;)&quot; style=&quot;background:transparent; border:none; color:var(--text-muted); cursor:pointer; font-size:14px; padding:2px 4px; border-radius:4px;&quot; title=&quot;Close review panel&quot;&gt;✕&lt;/button&gt;
      &lt;/div&gt;
      &lt;div style=&quot;flex:1; overflow-y:auto; padding:8px; display:flex; flex-direction:column; gap:6px;&quot;&gt;
        &lt;div style=&quot;padding:10px 12px; border-radius:8px; background:var(--warning-bg); border:1.5px solid var(--warning-border); cursor:pointer;&quot;&gt;
          &lt;div style=&quot;display:flex; justify-content:space-between; align-items:center; font-weight:800; font-size:12px;&quot;&gt;
            &lt;span&gt;Page 14 (Active)&lt;/span&gt;
            &lt;span style=&quot;color:var(--warning); font-size:11px; font-weight:700;&quot;&gt;Needs Review · 71%&lt;/span&gt;
          &lt;/div&gt;
        &lt;/div&gt;
        &lt;div style=&quot;padding:10px 12px; border-radius:8px; background:var(--bg-subtle); border:1px solid var(--border); cursor:pointer;&quot;&gt;
          &lt;div style=&quot;display:flex; justify-content:space-between; align-items:center; font-weight:800; font-size:12px;&quot;&gt;
            &lt;span&gt;Page 22&lt;/span&gt;
            &lt;span style=&quot;color:var(--warning); font-size:11px; font-weight:700;&quot;&gt;Needs Review · 78%&lt;/span&gt;
          &lt;/div&gt;
        &lt;/div&gt;
        &lt;div style=&quot;padding:10px 12px; border-radius:8px; background:var(--bg-subtle); border:1px solid var(--border); cursor:pointer;&quot;&gt;
          &lt;div style=&quot;display:flex; justify-content:space-between; align-items:center; font-weight:800; font-size:12px;&quot;&gt;
            &lt;span&gt;Page 39&lt;/span&gt;
            &lt;span style=&quot;color:var(--warning); font-size:11px; font-weight:700;&quot;&gt;Needs Review · 82%&lt;/span&gt;
          &lt;/div&gt;
        &lt;/div&gt;
      &lt;/div&gt;`;

// Normalize for matching
const normS2Srcdoc = s2Srcdoc.replace(/\r\n/g, '\n');
const normRailReview = s2RailReview.replace(/\r\n/g, '\n');

if (!normS2Srcdoc.includes(normRailReview)) {
  console.error('Could not find rail review match in Slide 2 srcdoc!');
  process.exit(1);
}

let newS3Srcdoc = normS2Srcdoc.replace(normRailReview, s3ReviewPanel.replace(/\r\n/g, '\n'));

// In workspace-panes:
// Change Scan pane header to Page 14:
newS2Srcdoc = newS3Srcdoc;
const oldScanHeader = `&lt;span&gt;Original Scan (Page 3 of 42)&lt;/span&gt;
          &lt;span class=&quot;quality-pill-good&quot;&gt;High Quality · 96%&lt;/span&gt;`;
const newScanHeader = `&lt;span&gt;Original Scan (Page 14 of 42)&lt;/span&gt;
          &lt;span style=&quot;color:var(--warning); font-size:11px; font-weight:700;&quot;&gt;Needs Review · 71%&lt;/span&gt;`;

newS3Srcdoc = newS3Srcdoc.replace(oldScanHeader, newScanHeader);

// In Scan sheet:
const oldScanSheetContent = `&lt;h2&gt;2. Rent and Security Deposit&lt;/h2&gt;
            &lt;p&gt;&lt;strong&gt;2.1 Base Monthly Rent.&lt;/strong&gt; The Tenant shall pay to Landlord a base monthly rent of &lt;strong&gt;$4,500.00 USD&lt;/strong&gt;, payable in advance on or before the first day of each calendar month during the lease term. All payments shall be made via wire transfer or ACH.&lt;/p&gt;
            &lt;p&gt;&lt;strong&gt;2.2 Escalation Schedule.&lt;/strong&gt; Commencing on the anniversary of the Commencement Date, base monthly rent shall adjust according to Table 1 below:&lt;/p&gt;
            &lt;table&gt;
              &lt;thead&gt;
                &lt;tr&gt;&lt;th&gt;Lease Period&lt;/th&gt;&lt;th&gt;Base Rent / Mo&lt;/th&gt;&lt;th&gt;CAM Charges&lt;/th&gt;&lt;th&gt;Total Monthly&lt;/th&gt;&lt;/tr&gt;
              &lt;/thead&gt;
              &lt;tbody&gt;
                &lt;tr&gt;&lt;td&gt;Months 1–12&lt;/td&gt;&lt;td&gt;$4,500.00&lt;/td&gt;&lt;td&gt;$650.00&lt;/td&gt;&lt;td&gt;$5,150.00&lt;/td&gt;&lt;/tr&gt;
                &lt;tr&gt;&lt;td&gt;Months 13–24&lt;/td&gt;&lt;td&gt;$4,680.00&lt;/td&gt;&lt;td&gt;$675.00&lt;/td&gt;&lt;td&gt;$5,355.00&lt;/td&gt;&lt;/tr&gt;
                &lt;tr&gt;&lt;td&gt;Months 25–36&lt;/td&gt;&lt;td&gt;$4,867.20&lt;/td&gt;&lt;td&gt;$702.00&lt;/td&gt;&lt;td&gt;$5,569.20&lt;/td&gt;&lt;/tr&gt;
              &lt;/tbody&gt;
            &lt;/table&gt;
            &lt;p&gt;&lt;strong&gt;2.3 Security Deposit.&lt;/strong&gt; Upon execution of this Agreement, Tenant shall deposit with Landlord the sum of &lt;strong&gt;$9,000.00 USD&lt;/strong&gt; as security for the full performance of Tenant covenants under Clause 4.2.&lt;/p&gt;
            &lt;div class=&quot;search-match-scan&quot; style=&quot;top:38px; left:34px; width:190px; height:22px;&quot;&gt;&lt;/div&gt;
            &lt;div class=&quot;search-match-scan&quot; style=&quot;bottom:120px; left:34px; width:110px; height:20px;&quot;&gt;&lt;/div&gt;`;

const newScanSheetContent = `&lt;h2&gt;Payment Milestone Schedule&lt;/h2&gt;
            &lt;p&gt;Milestone 1: &lt;strong&gt;$1,250,000.00&lt;/strong&gt; upon approval of detailed project report (DPR).&lt;/p&gt;
            &lt;p&gt;Milestone 2: &lt;strong&gt;$3,500,000.00&lt;/strong&gt; upon delivery of machinery at site.&lt;/p&gt;`;

newS3Srcdoc = newS3Srcdoc.replace(oldScanSheetContent, newScanSheetContent);

// In Editor pane header:
// Slide 2 has:
// <div class="pane-quiet-header">
//   <span>Extracted Text (Page 3 of 42)</span>
//   <span style="font-size:11px; color:var(--text-subtle);">TipTap Editor · Syncs with BBoxes</span>
// </div>

const oldEditorHeader = `&lt;div class=&quot;pane-quiet-header&quot;&gt;
          &lt;span&gt;Extracted Text (Page 3 of 42)&lt;/span&gt;
          &lt;span style=&quot;font-size:11px; color:var(--text-subtle);&quot;&gt;TipTap Editor · Syncs with BBoxes&lt;/span&gt;
        &lt;/div&gt;`;

const newEditorHeader = `&lt;div class=&quot;pane-quiet-header&quot;&gt;
          &lt;span&gt;Extracted Text (Page 14 of 42)&lt;/span&gt;
          &lt;div style=&quot;display:flex; gap:6px;&quot;&gt;
            &lt;button style=&quot;background:var(--bg-subtle); border:1px solid var(--border-strong); color:var(--text-main); padding:3px 8px; border-radius:5px; font-size:11px; font-weight:700; cursor:pointer;&quot; title=&quot;Admin only: Rerun OCR extraction for this page&quot;&gt;
              ⚡ Rerun &lt;span style=&quot;font-size:9.5px; opacity:0.75; font-weight:600; background:var(--border); padding:1px 4px; border-radius:3px;&quot;&gt;Admin&lt;/span&gt;
            &lt;/button&gt;
            &lt;button style=&quot;background:var(--success); color:#fff; border:none; padding:3px 9px; border-radius:5px; font-size:11px; font-weight:700; cursor:pointer;&quot; title=&quot;Mark verified and advance to next page&quot;&gt;
              ✓ Mark Verified &amp;amp; Next
            &lt;/button&gt;
          &lt;/div&gt;
        &lt;/div&gt;`;

newS3Srcdoc = newS3Srcdoc.replace(oldEditorHeader, newEditorHeader);

// In Editor doc:
const oldEditorDocContent = `&lt;h2&gt;2. Rent and &lt;mark class=&quot;search-match-text&quot;&gt;Security Deposit&lt;/mark&gt;&lt;/h2&gt;
            &lt;p&gt;&lt;strong&gt;2.1 Base Monthly Rent.&lt;/strong&gt; The Tenant shall pay to Landlord a base monthly rent of &lt;strong&gt;$4,500.00 USD&lt;/strong&gt;, payable in advance on or before the first day of each calendar month during the lease term. All payments shall be made via wire transfer or ACH.&lt;/p&gt;
            &lt;p&gt;&lt;strong&gt;2.2 Escalation Schedule.&lt;/strong&gt; Commencing on the anniversary of the Commencement Date, base monthly rent shall adjust according to Table 1 below:&lt;/p&gt;
            &lt;table&gt;
              &lt;thead&gt;
                &lt;tr&gt;&lt;th&gt;Lease Period&lt;/th&gt;&lt;th&gt;Base Rent / Mo&lt;/th&gt;&lt;th&gt;CAM Charges&lt;/th&gt;&lt;th&gt;Total Monthly&lt;/th&gt;&lt;/tr&gt;
              &lt;/thead&gt;
              &lt;tbody&gt;
                &lt;tr&gt;&lt;td&gt;Months 1–12&lt;/td&gt;&lt;td&gt;$4,500.00&lt;/td&gt;&lt;td&gt;$650.00&lt;/td&gt;&lt;td&gt;$5,150.00&lt;/td&gt;&lt;/tr&gt;
                &lt;tr&gt;&lt;td&gt;Months 13–24&lt;/td&gt;&lt;td&gt;$4,680.00&lt;/td&gt;&lt;td&gt;$675.00&lt;/td&gt;&lt;td&gt;$5,355.00&lt;/td&gt;&lt;/tr&gt;
                &lt;tr&gt;&lt;td&gt;Months 25–36&lt;/td&gt;&lt;td&gt;$4,867.20&lt;/td&gt;&lt;td&gt;$702.00&lt;/td&gt;&lt;td&gt;$5,569.20&lt;/td&gt;&lt;/tr&gt;
              &lt;/tbody&gt;
            &lt;/table&gt;
            &lt;p&gt;&lt;strong&gt;2.3 &lt;mark class=&quot;search-match-text&quot;&gt;Security Deposit&lt;/mark&gt;.&lt;/strong&gt; Upon execution of this Agreement, Tenant shall deposit with Landlord the sum of &lt;strong&gt;$9,000.00 USD&lt;/strong&gt; as security for the full performance of Tenant covenants under Clause 4.2.&lt;/p&gt;
            &lt;div class=&quot;page-break-divider&quot;&gt;
              &lt;div class=&quot;page-break-line&quot;&gt;&lt;/div&gt;
              &lt;span class=&quot;page-break-tag&quot;&gt;📄 Page Break · Page 3 ➔ Page 4&lt;/span&gt;
              &lt;div class=&quot;page-break-line&quot;&gt;&lt;/div&gt;
            &lt;/div&gt;
            &lt;h2&gt;3. Use of Premises &amp; Permitted Occupancy&lt;/h2&gt;
            &lt;p&gt;Tenant shall use and occupy the Premises solely for general commercial office and research purposes in compliance with local municipal zoning ordinances.&lt;/p&gt;`;

const newEditorDocContent = `&lt;h2&gt;Payment Milestone Schedule&lt;/h2&gt;
            &lt;p&gt;Milestone 1: $1,250,000.00 upon approval of DPR.&lt;/p&gt;
            &lt;p&gt;Milestone 2: $3,500,000.00 upon delivery of machinery.&lt;/p&gt;`;

newS3Srcdoc = newS3Srcdoc.replace(oldEditorDocContent, newEditorDocContent);

// In HUD:
newS3Srcdoc = newS3Srcdoc.replace('value=&quot;3&quot;', 'value=&quot;14&quot;');

console.log('Constructed new Slide 3 srcdoc, length:', newS3Srcdoc.length);

// Now replace Slide 3 in content:
const m3 = content.match(/id=["']slide-3["'][\s\S]*?<iframe[^>]*srcdoc="[\s\S]*?"[\s\S]*?<\/iframe>/i);
if (!m3) {
  console.error('Slide 3 iframe not found in content!');
  process.exit(1);
}

const oldS3Iframe = m3[0];
const newS3Iframe = oldS3Iframe.replace(/srcdoc="[\s\S]*?"/i, `srcdoc="${newS3Srcdoc}"`);

let newContent = content.replace(oldS3Iframe, newS3Iframe);

// Also update Slide 3 card header and rationale box outside iframe:
const oldCardHeader = `<div class="board-desc">Step-by-step proofreading for flagged low-confidence pages. General users edit and correct text directly; administrators have page Rerun controls.</div>
      </div>
      <div class="fix-rationale-box">
        <div class="fix-points">
          <span class="fix-badge">Fix: Guided Review Wizard</span>
          <span class="fix-text">Users verify low-confidence pages in a dedicated step-by-step queue.</span>
          <span class="fix-badge">Fix: Proofread &amp; Edit Focus</span>
          <span class="fix-text">General users proofread &amp; edit text directly; the [⚡ Rerun] action is role-gated strictly for Admins.</span>
          <span class="fix-badge">Fix: Real Page-Level Metrics</span>
          <span class="fix-text">Displays actual backend page confidence (e.g. 71% · Needs Review); zero fictional descriptions.</span>
          <span class="fix-badge enhanced">✨ New: Enabled WYSIWYG Editor</span>
          <span class="fix-text">Explicit [✎ Edit Mode: ON] toggle with rich formatting ribbon and highlighted suspect tokens allows immediate in-place correction of low-confidence OCR text.</span>
        </div>`;

const newCardHeader = `<div class="board-desc">Step-by-step proofreading for flagged low-confidence pages directly within the Document Studio. Review section opens on the left while keeping the clean Studio workspace intact.</div>
      </div>
      <div class="fix-rationale-box">
        <div class="fix-points">
          <span class="fix-badge">Fix: Native Studio Review Drawer</span>
          <span class="fix-text">Review Queue opens seamlessly on the left without cluttering the UI, matching the clean Slide 02 Document Studio layout.</span>
          <span class="fix-badge">Fix: Identical TipTap Editor</span>
          <span class="fix-text">Maintains the identical rich editor from Slide 02 for effortless in-place proofreading and text correction.</span>
          <span class="fix-badge">Fix: Role Separation</span>
          <span class="fix-text">General users proofread &amp; edit text directly before clicking [✓ Mark Verified &amp; Next]; [⚡ Rerun] is role-gated for Admins.</span>
        </div>`;

newContent = newContent.replace(oldCardHeader, newCardHeader);

fs.writeFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', newContent, 'utf8');
console.log('Successfully updated Slide 3 in mockup file!');
