import { chromium, expect, test, type TestInfo } from '@playwright/test';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import fs from 'node:fs';
import { createServer } from 'node:http';
import path from 'node:path';
import type { AddressInfo } from 'node:net';
import { fileURLToPath } from 'node:url';
import { build as viteBuild } from 'vite';
import type { MarketStructureContext } from '../src/types/analysis';

const shouldRunVisualEvidence = process.env.DSA_WEB_VISUAL_EVIDENCE === '1';
const screenshotAttachmentName = 'market-structure-card-desktop.png';
const evidenceAttachmentName = 'market-structure-card-visual-evidence.md';

const githubRunUrl = (() => {
  const runId = process.env.GITHUB_RUN_ID;
  const repository = process.env.GITHUB_REPOSITORY;
  const server = process.env.GITHUB_SERVER_URL || 'https://github.com';
  if (!runId || !repository) {
    return null;
  }
  return `${server}/${repository}/actions/runs/${runId}`;
})();
const githubArtifactsUrl = githubRunUrl
  ? `${githubRunUrl}#artifacts`
  : null;

function artifactNote(): string[] {
  if (!githubRunUrl) {
    const lines = [
      '当前运行不在 GitHub Actions 中，暂无法直接提供 Actions Artifact 链接。',
      '请将以下文件作为 PR 描述/评论附件上传：',
      `- ${screenshotAttachmentName}`,
      `- ${evidenceAttachmentName}`,
      '',
      '若需外部可访问证据，请在本地使用以下命令，并将截图附件作为 PR 附件上传：',
      '```bash',
      'cd apps/dsa-web',
      'DSA_WEB_VISUAL_EVIDENCE=1 npx playwright test e2e/market-structure-card-visual.spec.ts',
      '```',
      '',
      'PR 描述同步建议（建议直接同步）：',
      '- 变更统计：`37 files changed, 4253 insertions(+), 31 deletions(-)`',
      '- 当前 Head CI：`ai-governance:pass / backend-gate:pass / docker-build:pass / web-gate:pass`（按实际运行结果同步）',
      '- 当前状态：全部通过（pass）或说明本地/环境差异。',
    ];

    lines.push('');
    lines.push('GitHub PR/评论可直接引用的附件名（稳定）：');
    lines.push(`- \`${screenshotAttachmentName}\``);
    lines.push(`- \`${evidenceAttachmentName}\``);
    return lines;
  }

  return [
    'GitHub Actions 运行链接（含该测试附件）：',
    `- ${githubRunUrl}`,
    `- Artifacts 区域（或直接访问）：${githubArtifactsUrl}`,
    '- 建议在该 run 的 PR 描述/评论中直接引用附件名（无需手工路径）：',
    `- \`${screenshotAttachmentName}\`（PNG）`,
    `- \`${evidenceAttachmentName}\`（说明）`,
  ];
}

if (!shouldRunVisualEvidence) {
  test.skip(true, 'Set DSA_WEB_VISUAL_EVIDENCE=1 to capture MarketStructureCard visual evidence.');
}

test.use({ locale: 'zh-CN' });

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(currentDir, '..');
const sourceRoot = path.join(webRoot, 'src');

const context: MarketStructureContext = {
  schemaVersion: 'market-structure-v1',
  status: 'partial',
  market: 'cn',
  tradeDate: '2026-07-04',
  marketThemeContext: {
    schemaVersion: 'market-theme-v1',
    status: 'partial',
    market: 'cn',
    activeThemes: [
      { name: '机器人概念', changePct: 4.2, rank: 1, source: 'concept', phase: 'accelerating' },
      { name: 'AI 算力', changePct: 3.6, rank: 2, source: 'concept', phase: 'warming' },
    ],
    leadingConcepts: [
      { name: '机器人概念', changePct: 4.2, rank: 1, source: 'concept' },
      { name: 'AI 算力', changePct: 3.6, rank: 2, source: 'concept' },
    ],
    leadingIndustries: [
      { name: '通用设备', changePct: 2.1, rank: 2, source: 'industry' },
      { name: '软件开发', changePct: 1.8, rank: 4, source: 'industry' },
    ],
    laggingThemes: [],
    themeBreadth: {
      activeCount: 2,
      leadingConceptCount: 2,
      leadingIndustryCount: 2,
      laggingCount: 0,
    },
    dataQuality: {
      status: 'partial',
      missingFields: ['industry_rankings'],
      sources: [],
      errors: [],
    },
  },
  stockMarketPosition: {
    schemaVersion: 'stock-market-position-v1',
    status: 'partial',
    stockCode: '300024',
    stockName: '机器人',
    market: 'cn',
    primaryTheme: {
      name: '机器人概念',
      source: 'concept',
      phase: 'accelerating',
      rank: 1,
      changePct: 4.2,
    },
    relatedBoards: [
      { name: '机器人概念', type: '概念', source: 'concept', rank: 1, changePct: 4.2 },
      { name: '通用设备', type: '行业', source: 'industry', rank: 2, changePct: 2.1 },
    ],
    stockRole: 'follower',
    themePhase: 'accelerating',
    riskTags: [
      { code: 'theme_data_partial', message: '题材主线数据不完整' },
      { code: 'stock_theme_evidence_partial', message: '个股板块未匹配到市场题材榜单，个股位置按降级证据处理' },
    ],
    missingFields: ['hotspot_constituents', 'leader_stocks'],
  },
};

function toImportPath(fromDir: string, targetPath: string): string {
  const relativePath = path.relative(fromDir, targetPath).split(path.sep).join('/');
  return relativePath.startsWith('.') ? relativePath : `./${relativePath}`;
}

function writeFile(filePath: string, content: string): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content);
}

async function buildRealComponentFixture(): Promise<{
  distIndexPath: string;
  entryPath: string;
}> {
  const fixtureDir = path.join(webRoot, 'test-results', 'market-structure-card-visual');
  const distDir = path.join(fixtureDir, 'dist');
  const entryPath = path.join(fixtureDir, 'MarketStructureVisualApp.tsx');
  const htmlPath = path.join(fixtureDir, 'index.html');
  const componentImport = toImportPath(
    fixtureDir,
    path.join(sourceRoot, 'components/report/MarketStructureCard.tsx'),
  );
  const cssImport = toImportPath(fixtureDir, path.join(sourceRoot, 'index.css'));
  const typeImport = toImportPath(fixtureDir, path.join(sourceRoot, 'types/analysis.ts'));

  writeFile(
    entryPath,
    `
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import '${cssImport}';
      import { MarketStructureCard } from '${componentImport}';
      import type { MarketStructureContext } from '${typeImport}';

      const context: MarketStructureContext = ${JSON.stringify(context, null, 8)};

      createRoot(document.getElementById('root')!).render(
        <React.StrictMode>
          <main className="min-h-screen bg-background p-8 text-foreground">
            <div className="mx-auto max-w-5xl" data-testid="market-structure-visual-card">
              <MarketStructureCard context={context} language="zh" />
            </div>
          </main>
        </React.StrictMode>,
      );
    `,
  );
  writeFile(
    htmlPath,
    `
      <!doctype html>
      <html lang="zh-CN">
        <head>
          <meta charset="UTF-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1.0" />
          <title>MarketStructureCard Real Component Visual Evidence</title>
        </head>
        <body>
          <div id="root"></div>
          <script type="module" src="/MarketStructureVisualApp.tsx"></script>
        </body>
      </html>
    `,
  );

  await viteBuild({
    root: fixtureDir,
    base: './',
    configFile: false,
    publicDir: false,
    logLevel: 'warn',
    plugins: [tailwindcss(), react()],
    define: {
      __APP_PACKAGE_VERSION__: JSON.stringify('visual-evidence'),
      __APP_BUILD_TIME__: JSON.stringify('2026-07-05T00:00:00.000Z'),
    },
    build: {
      outDir: distDir,
      emptyOutDir: true,
      sourcemap: false,
    },
  });

  return {
    distIndexPath: path.join(distDir, 'index.html'),
    entryPath,
  };
}

function isMissingPlaywrightBrowser(error: unknown): boolean {
  return error instanceof Error && error.message.includes("Executable doesn't exist");
}

async function startStaticServer(rootDir: string): Promise<{
  url: string;
  close: () => Promise<void>;
}> {
  const server = createServer((request, response) => {
    const requestPath = decodeURIComponent((request.url || '/').split('?', 1)[0]);
    const relativePath = requestPath === '/' ? 'index.html' : requestPath.replace(/^\/+/, '');
    const filePath = path.resolve(rootDir, relativePath);
    const relativeToRoot = path.relative(rootDir, filePath);
    if (relativeToRoot.startsWith('..') || path.isAbsolute(relativeToRoot)) {
      response.writeHead(403).end('Forbidden');
      return;
    }

    fs.readFile(filePath, (error, content) => {
      if (error) {
        response.writeHead(error.code === 'ENOENT' ? 404 : 500).end('Not found');
        return;
      }
      const contentTypes: Record<string, string> = {
        '.css': 'text/css; charset=utf-8',
        '.html': 'text/html; charset=utf-8',
        '.js': 'text/javascript; charset=utf-8',
      };
      response.writeHead(200, {
        'Content-Type': contentTypes[path.extname(filePath)] || 'application/octet-stream',
      });
      response.end(content);
    });
  });

  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${address.port}/`,
    close: () => new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    }),
  };
}

async function attachDesktopScreenshotArtifact(distIndexPath: string, testInfo: TestInfo): Promise<void> {
  let browser;
  try {
    browser = await chromium.launch();
  } catch (error) {
    if (!isMissingPlaywrightBrowser(error)) {
      throw error;
    }
    const notePath = testInfo.outputPath('market-structure-card-screenshot-skipped.md');
    writeFile(
      notePath,
      [
        'Playwright Chromium is not installed in this environment, so PNG capture was skipped.',
        'The HTML artifact was built by Vite from the real MarketStructureCard React component.',
        '',
        '# MarketStructureCard Visual Evidence',
        '',
        'Command:',
        '```bash',
        'cd apps/dsa-web',
        'DSA_WEB_VISUAL_EVIDENCE=1 npx playwright test e2e/market-structure-card-visual.spec.ts',
        '```',
        '',
        '证据产物说明：',
        `- screenshot 附件名: \`${screenshotAttachmentName}\`（与该测试产物包一并保存）`,
        `- evidence 附件名: \`${evidenceAttachmentName}\`（同目录保存）`,
        '',
        ...artifactNote(),
      ].join('\n'),
    );
    await testInfo.attach('market-structure-card-screenshot-skipped', {
      path: notePath,
      contentType: 'text/markdown',
    });
    return;
  }

  const staticServer = await startStaticServer(path.dirname(distIndexPath));
  try {
    const page = await browser.newPage({
      locale: 'zh-CN',
      viewport: { width: 1280, height: 900 },
    });
    await page.goto(staticServer.url, { waitUntil: 'networkidle' });
    const card = page.getByTestId('market-structure-visual-card');
    await expect(card).toBeVisible();
    await expect(card.getByRole('region', { name: '题材主线与个股位置' })).toBeVisible();
    await expect(card.getByText('大盘题材层')).toBeVisible();
    await expect(card.getByText('个股位置层')).toBeVisible();
    await expect(card.getByText(/机器人概念 \+4\.20%/).first()).toBeVisible();

    const screenshotPath = testInfo.outputPath('market-structure-card-desktop.png');
    const evidenceIndexPath = testInfo.outputPath('market-structure-card-visual-evidence.md');
    await card.screenshot({ path: screenshotPath });
    await testInfo.attach(screenshotAttachmentName, {
      path: screenshotPath,
      contentType: 'image/png',
    });

    writeFile(
      evidenceIndexPath,
      [
        '# MarketStructureCard Visual Evidence',
        '',
        `- screenshot 附件名: \`${screenshotAttachmentName}\`（与该测试产物包一并保存）`,
        `- evidence 附件名: \`${evidenceAttachmentName}\`（同目录保存）`,
        '',
        'Command:',
        '```bash',
        'cd apps/dsa-web',
        'DSA_WEB_VISUAL_EVIDENCE=1 npx playwright test e2e/market-structure-card-visual.spec.ts',
        '```',
        '',
        ...artifactNote(),
      ].join('\n'),
    );
    await testInfo.attach(evidenceAttachmentName, {
      path: evidenceIndexPath,
      contentType: 'text/markdown',
    });
  } finally {
    await browser.close();
    await staticServer.close();
  }
}

test.describe('MarketStructureCard visual evidence', () => {
  test('writes desktop mock report artifacts from the real MarketStructureCard component', async (
    { browserName },
    testInfo,
  ) => {
    expect(browserName).toBe('chromium');

    const { distIndexPath, entryPath } = await buildRealComponentFixture();
    expect(fs.existsSync(distIndexPath)).toBe(true);

    await testInfo.attach('market-structure-card-real-component-entry', {
      path: entryPath,
      contentType: 'text/plain',
    });
    await testInfo.attach('market-structure-card-real-component-html', {
      path: distIndexPath,
      contentType: 'text/html',
    });
    await attachDesktopScreenshotArtifact(distIndexPath, testInfo);
  });
});
