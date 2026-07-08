import { describe, it, expect } from 'vitest';
import { ConceptValueSchema, ConceptValueRequiredSchema } from './zod';

// A helper to produce a valid UUID v4-like string for tests.
const uuid = '3c144c58-3e36-4f1f-965f-2c88f18c2a0d';

const validCollectionItem = (overrides: Partial<any> = {}) => ({
    key: 'k1',
    label: 'Label 1',
    conceptid: 'c1',
    sortOrder: '1',
    children: [],
    ...overrides,
});

const validConcept = (overrides: Partial<any> = {}) => ({
    display_value: 'Foo',
    node_value: uuid,
    details: [
        validCollectionItem({
            key: 'k2',
            label: 'Parent',
            conceptid: 'c-parent',
            sortOrder: '10',
            children: [
                validCollectionItem({
                    key: 'k3',
                    label: 'Child',
                    conceptid: 'c-child',
                    sortOrder: '11',
                }),
            ],
        }),
    ],
    ...overrides,
});

describe('ConceptValueSchema (ConceptValue)', () => {
    it('parses a valid ConceptValue object (with nested children)', () => {
        const parsed = ConceptValueSchema.parse(validConcept());
        expect(parsed.display_value).toBe('Foo');
        expect(parsed.node_value).toBe(uuid);
        expect(Array.isArray(parsed.details)).toBe(true);
        expect(parsed.details[0].children?.length).toBe(1);
        expect(parsed.details[0].children?.[0].label).toBe('Child');
    });

    it('allows node_value to be null', () => {
        const parsed = ConceptValueSchema.parse(
            validConcept({ node_value: null }),
        );
        expect(parsed.node_value).toBeNull();
    });

    it('rejects non-UUID node_value strings', () => {
        const bad = validConcept({ node_value: 'not-a-uuid' });
        expect(() => ConceptValueSchema.parse(bad)).toThrow();
    });

    it('requires display_value to be a string', () => {
        const bad = validConcept({ display_value: 123 });
        expect(() => ConceptValueSchema.parse(bad as any)).toThrow();
    });

    // Current schema permits `sortOrder` to be nullish; interface says string.
    // This documents current behavior.
    it('accepts nullish sortOrder per current schema', () => {
        const withNullSort = validConcept({
            details: [validCollectionItem({ sortOrder: null })],
        });
        const parsed = ConceptValueSchema.parse(withNullSort);
        expect(parsed.details[0].sortOrder).toBeNull();
    });
});

describe('ConceptValueRequiredSchema (node_value required & uuid v4)', () => {
    it('accepts when node_value is a UUID v4 string', () => {
        const parsed = ConceptValueRequiredSchema.parse(
            validConcept({ node_value: uuid }),
        );
        expect(parsed.node_value).toBe(uuid);
    });

    it('rejects null or empty node_value', () => {
        const nullNode = validConcept({ node_value: null });
        const emptyNode = validConcept({ node_value: '' });
        expect(() => ConceptValueRequiredSchema.parse(nullNode)).toThrow();
        expect(() => ConceptValueRequiredSchema.parse(emptyNode)).toThrow();
    });

    it('rejects malformed UUID even when non-empty', () => {
        const bad = validConcept({ node_value: '1234' });
        expect(() => ConceptValueRequiredSchema.parse(bad)).toThrow();
    });
});
