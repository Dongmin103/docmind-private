import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

const SourceRoot = path.join(process.cwd(), 'src');
const RetiredOwner =
  /^(?:@\/pages\/(?:agent|agents|dataset|datasets|memory|memories|skills|chunk|dataflow-result)(?:\/|$)|@\/pages\/user-setting\/(?:mcp|compilation-templates)(?:\/|$)|@\/services\/(?:agent|memory|mcp-server|knowledge|document-structure|dataset-|compilation-template)|@\/hooks\/use-(?:agent|memory|mcp|knowledge|dataset|compilation-template)|@\/components\/(?:canvas|artifact-force-graph|structure-graph|indented-tree|jsonjoy-builder|json-edit)|@\/lib\/editor)/;
const RetiredPackage =
  /^(?:@antv\/|@xyflow\/|@lexical\/|@monaco-editor\/|monaco-editor$|lexical$|jsoneditor$|react-force-graph-2d$)/;

function resolveImport(owner: string, specifier: string): string | undefined {
  if (!specifier.startsWith('@/') && !specifier.startsWith('.'))
    return undefined;
  const base = specifier.startsWith('@/')
    ? path.join(SourceRoot, specifier.slice(2))
    : path.resolve(path.dirname(owner), specifier);
  return [
    base,
    ...['.ts', '.tsx', '.js', '.jsx', '.json'].map((ext) => base + ext),
    ...['.ts', '.tsx', '.js', '.jsx'].map((ext) =>
      path.join(base, 'index' + ext),
    ),
  ].find(
    (candidate) => fs.existsSync(candidate) && fs.statSync(candidate).isFile(),
  );
}

it.each([
  'pages/docmind/index.tsx',
  'pages/docmind/workspace-views.tsx',
  'components/chunk-inspector/index.tsx',
  'pages/docmind/workspace-settings.tsx',
  'components/message-item/index.tsx',
])(
  'keeps the actual retained import graph for %s free of retired owners',
  (entry) => {
    const visited = new Set<string>();
    const inspect = (filename: string) => {
      if (visited.has(filename) || !/\.[jt]sx?$/.test(filename)) return;
      visited.add(filename);
      const source = ts.createSourceFile(
        filename,
        fs.readFileSync(filename, 'utf8'),
        ts.ScriptTarget.Latest,
        true,
      );
      const walk = (node: ts.Node) => {
        const specifier =
          ts.isImportDeclaration(node) || ts.isExportDeclaration(node)
            ? node.moduleSpecifier
            : ts.isCallExpression(node) &&
                node.expression.kind === ts.SyntaxKind.ImportKeyword
              ? node.arguments[0]
              : undefined;
        if (specifier && ts.isStringLiteral(specifier)) {
          const name = specifier.text;
          expect(name).not.toMatch(RetiredOwner);
          expect(name).not.toMatch(RetiredPackage);
          const next = resolveImport(filename, name);
          const localCode =
            (name.startsWith('@/') || name.startsWith('.')) &&
            (!path.extname(name) || /\.[jt]sx?$/.test(name));
          if (localCode) {
            expect(next).toBeDefined();
            if (next) inspect(next);
          }
        }
        ts.forEachChild(node, walk);
      };
      walk(source);
    };
    inspect(path.join(SourceRoot, entry));
    expect(visited.has(path.join(SourceRoot, entry))).toBe(true);
  },
);

it('keeps exclusive editor packages and Monaco assets out of the manifest and Vite copy targets', () => {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(process.cwd(), 'package.json'), 'utf8'),
  );
  for (const name of Object.keys(manifest.dependencies))
    expect(name).not.toMatch(RetiredPackage);
  expect(
    fs.readFileSync(path.join(process.cwd(), 'vite.config.ts'), 'utf8'),
  ).not.toContain('monaco-editor/min/vs');
});
