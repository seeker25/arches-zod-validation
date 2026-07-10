import { describe, it, expect } from 'vitest';
import {
    ConceptListValueSchema,
    ConceptListValueRequiredSchema,
    getConceptListValueSchema,
    getConceptListValueRequiredSchema,
} from './zod';

// A helper to produce valid UUID v4-like strings for tests.
const uuid1 = '3c144c58-3e36-4f1f-965f-2c88f18c2a0d';
const uuid2 = '7b1f2a9e-4d6c-4a1e-9f18-6c8a0d8e2f77';

const validCollectionItem = (overrides: Partial<any> = {}) => ({
    key: 'k1',
    label: 'Label 1',
    conceptid: 'c1',
    sortOrder: '1',
    children: [],
    ...overrides,
});

const validConceptList = (overrides: Partial<any> = {}) => ({
    display_value: 'Foo; Bar',
    node_value: [uuid1, uuid2],
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

describe('ConceptListValueSchema (ConceptListValue)', () => {
    it('parses a valid ConceptListValue object (with nested children)', () => {
        const parsed = ConceptListValueSchema.parse(validConceptList());
        expect(parsed.display_value).toBe('Foo; Bar');
        expect(parsed.node_value).toEqual([uuid1, uuid2]);
        expect(Array.isArray(parsed.details)).toBe(true);
        expect(parsed.details[0].children?.length).toBe(1);
        expect(parsed.details[0].children?.[0].label).toBe('Child');
    });

    it('rejects when node_value contains a non-UUID', () => {
        const bad = validConceptList({ node_value: [uuid1, 'not-a-uuid'] });
        expect(() => ConceptListValueSchema.parse(bad)).toThrow();
    });

    it('requires display_value to be a string', () => {
        const bad = validConceptList({ display_value: 123 });
        expect(() => ConceptListValueSchema.parse(bad as any)).toThrow();
    });

    // Current schema permits `sortOrder` to be nullish; interface says string.
    // This documents current behavior.
    it('accepts nullish sortOrder per current schema', () => {
        const withNullSort = validConceptList({
            details: [validCollectionItem({ sortOrder: null })],
        });
        const parsed = ConceptListValueSchema.parse(withNullSort);
        expect(parsed.details[0].sortOrder).toBeNull();
    });
});

describe('ConceptListValueRequiredSchema (node_value required & uuid v4 array)', () => {
    it('accepts when node_value contains at least one UUID v4 string', () => {
        const parsed = ConceptListValueRequiredSchema.parse(
            validConceptList({ node_value: [uuid1] }),
        );
        expect(parsed.node_value).toEqual([uuid1]);
    });

    it('rejects empty node_value array', () => {
        const empty = validConceptList({ node_value: [] });
        expect(() => ConceptListValueRequiredSchema.parse(empty)).toThrow();
    });

    it('rejects when node_value contains malformed UUID even if array is non-empty', () => {
        const bad = validConceptList({ node_value: ['1234'] });
        expect(() => ConceptListValueRequiredSchema.parse(bad)).toThrow();
    });
});

describe('getConceptListValueSchema (optional node_value with max length)', () => {
    it('accepts a valid array within the maxLength', () => {
        const schema = getConceptListValueSchema(3);
        const parsed = schema.parse(
            validConceptList({ node_value: [uuid1, uuid2] }),
        );
        expect(parsed.node_value).toEqual([uuid1, uuid2]);
    });

    it('rejects when node_value exceeds maxLength', () => {
        const schema = getConceptListValueSchema(1);
        const bad = validConceptList({ node_value: [uuid1, uuid2] });
        expect(() => schema.parse(bad)).toThrow();
    });

    it('accepts null node_value', () => {
        const schema = getConceptListValueSchema(3);
        const parsed = schema.parse(validConceptList({ node_value: null }));
        expect(parsed.node_value).toBeNull();
    });

    it('rejects when node_value contains a non-UUID', () => {
        const schema = getConceptListValueSchema(3);
        const bad = validConceptList({ node_value: ['not-a-uuid'] });
        expect(() => schema.parse(bad)).toThrow();
    });
});

describe('getConceptListValueRequiredSchema (required node_value with max length)', () => {
    it('accepts a valid array within the maxLength', () => {
        const schema = getConceptListValueRequiredSchema(3);
        const parsed = schema.parse(validConceptList({ node_value: [uuid1] }));
        expect(parsed.node_value).toEqual([uuid1]);
    });

    it('rejects empty node_value array', () => {
        const schema = getConceptListValueRequiredSchema(3);
        const bad = validConceptList({ node_value: [] });
        expect(() => schema.parse(bad)).toThrow();
    });

    it('rejects when node_value exceeds maxLength', () => {
        const schema = getConceptListValueRequiredSchema(1);
        const bad = validConceptList({ node_value: [uuid1, uuid2] });
        expect(() => schema.parse(bad)).toThrow();
    });

    it('rejects when node_value contains a malformed UUID', () => {
        const schema = getConceptListValueRequiredSchema(3);
        const bad = validConceptList({ node_value: ['1234'] });
        expect(() => schema.parse(bad)).toThrow();
    });

    it('rejects null node_value', () => {
        const schema = getConceptListValueRequiredSchema(3);
        const bad = validConceptList({ node_value: null });
        expect(() => schema.parse(bad as any)).toThrow();
    });
});
