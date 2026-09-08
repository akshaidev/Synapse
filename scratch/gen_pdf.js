const puppeteer = require('puppeteer-core');
const path = require('path');

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
    ],
  });

  const page = await browser.newPage();

  // Load the HTML file
  const htmlPath = path.resolve('/Users/akshai/Developer/Synapse/presentation_cream.html');
  await page.goto(`file://${htmlPath}`, { waitUntil: 'networkidle0', timeout: 30000 });

  // Wait a moment for fonts to fully load
  await new Promise(r => setTimeout(r, 2000));

  // Generate PDF with exact 16:9 slide dimensions
  // 297mm x 167mm per slide (landscape widescreen)
  await page.pdf({
    path: '/Users/akshai/Developer/Synapse/Project_Synapse_SIH2026_Cream.pdf',
    width: '297mm',
    height: '167mm',
    printBackground: true,
    margin: { top: 0, right: 0, bottom: 0, left: 0 },
  });

  console.log('PDF generated successfully!');
  await browser.close();
})().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
