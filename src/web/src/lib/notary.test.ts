import { assert, expect, it } from 'vitest';
import sample from '@/test-fixtures/notary-accepted.json';
import { buildNotaryTopology } from './notary';
import type { NotaryDetails } from './types';

const details: NotaryDetails = {
  ...sample,
  status: 'Accepted',
};

it('connects the archive, signed bundle, directories and executable paths', () => {
  const topology = buildNotaryTopology(details);
  expect(topology.nodes).toHaveLength(6);
  expect(topology.edges).toHaveLength(5);
  expect(topology.signedPaths).toBe(3);
  const bundle = topology.nodes.find((node) => node.name.endsWith('.app'));
  assert.isDefined(bundle);
  expect(bundle.kind).toBe('bundle');
  expect(bundle.signatures).toHaveLength(1);
  expect(bundle.children[0].name).toBe('Contents');
  expect(topology.edges).toContainEqual(
    expect.objectContaining({
      source: 'archive',
      target: bundle.id,
    }),
  );
  const macOS = topology.nodes.find((node) => node.name === 'MacOS');
  assert.isDefined(macOS);
  for (const name of [
    'deeix-chat-server',
    'deeix-chat-desktop',
  ]) {
    const executable = topology.nodes.find((node) => node.name === name);
    assert.isDefined(executable);
    expect(topology.edges).toContainEqual(
      expect.objectContaining({
        source: macOS.id,
        target: executable.id,
      }),
    );
    expect(executable.x).toBeGreaterThan(macOS.x);
  }
});

it('merges duplicate records per path without losing their count or changing the log', () => {
  const topology = buildNotaryTopology(details);
  const server = topology.nodes.filter(
    (node) => node.name === 'deeix-chat-server',
  );
  expect(server).toHaveLength(1);
  expect(server[0].signatures).toHaveLength(1);
  expect(server[0].signatures[0].count).toBe(2);
  expect(details.ticketContents).toHaveLength(4);
  const sameHash = topology.nodes.filter((node) =>
    node.signatures.some(
      (value) => value.cdhash === sample.ticketContents[1].cdhash,
    ),
  );
  expect(sameHash).toHaveLength(2);
});

it('preserves different architecture signatures on the same path', () => {
  const topology = buildNotaryTopology({
    ...details,
    ticketContents: [
      ...sample.ticketContents,
      {
        ...sample.ticketContents[0],
        arch: 'x86_64',
        cdhash: 'intel-cdhash',
      },
    ],
  });
  const server = topology.nodes.find(
    (node) => node.name === 'deeix-chat-server',
  );
  assert.isDefined(server);
  expect(
    server.signatures.map((value) => [
      value.arch,
      value.count,
    ]),
  ).toEqual([
    [
      'arm64',
      2,
    ],
    [
      'x86_64',
      1,
    ],
  ]);
  expect(topology.nodes).toHaveLength(6);
});

it('handles empty tickets, paths without an archive prefix and missing architectures', () => {
  expect(
    buildNotaryTopology({
      ...details,
      ticketContents: null,
    }).edges,
  ).toEqual([]);
  const topology = buildNotaryTopology({
    ...details,
    ticketContents: [
      {
        ...sample.ticketContents[0],
        path: 'Installer.dmg',
        arch: null,
      },
    ],
  });
  expect(topology.nodes).toHaveLength(2);
  expect(topology.nodes[1].path).toBe('Installer.dmg');
  expect(topology.nodes[1].signatures[0].arch).toBeNull();
});
