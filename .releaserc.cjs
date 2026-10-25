function renderReference(context, reference) {
  const label = [
    reference.owner && `${reference.owner}/`,
    reference.repository,
    reference.issue && `${reference.prefix || "#"}${reference.issue}`,
  ].filter(Boolean).join("");

  if (!context.linkReferences) {
    return label;
  }

  const owner = reference.owner || context.owner;
  const repository = reference.repository || context.repository;
  const issuePath = context.issue || "issues";
  return `[${label}](${context.host}/${owner}/${repository}/${issuePath}/${reference.issue})`;
}

function commitPartial(context, commit) {
  const scope = commit.scope && `**${commit.scope}:**`;
  const subject = commit.subject || commit.header || "";
  const commitLink = commit.hash
    ? context.linkReferences
      ? `([${commit.shortHash}](${context.host}/${context.owner}/${context.repository}/${context.commit || "commit"}/${commit.hash}))`
      : commit.shortHash
    : "";
  const references = (commit.references || []).map((reference) => renderReference(context, reference));
  const line = [scope, subject, commitLink].filter(Boolean).join(" ");
  const referenceText = references.length ? `, closes ${references.join(" ")}` : "";
  const body = commit.raw?.body?.trim() || commit.body?.trim();

  return `${line}${referenceText}${body ? `\n\n${body}` : ""}`;
}

module.exports = {
  branches: [
    "main",
    {
      name: "beta",
      prerelease: true,
    },
  ],
  tagFormat: "v${version}",
  plugins: [
    "@semantic-release/commit-analyzer",
    [
      "@semantic-release/release-notes-generator",
      {
        writerOpts: {
          commitPartial,
        },
      },
    ],
    [
      "@semantic-release/exec",
      {
        prepareCmd: "NEW_VERSION=${nextRelease.version} && sed -i 's/\"version\": \".*\"/\"version\": \"'$NEW_VERSION'\"/' custom_components/ehs_sentinel/manifest.json && sed -i 's/sw_version = \".*\"/sw_version = \"'$NEW_VERSION'\"/' custom_components/ehs_sentinel/coordinator.py",
      },
    ],
    "@semantic-release/changelog",
    [
      "@semantic-release/git",
      {
        assets: [
          "custom_components/ehs_sentinel/manifest.json",
          "custom_components/ehs_sentinel/coordinator.py",
          "CHANGELOG.md",
        ],
        message: "release: ${nextRelease.version} [skip ci]",
      },
    ],
    "@semantic-release/github",
  ],
};