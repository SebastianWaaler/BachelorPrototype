/**
 * test_app.test.js
 *
 * Enhetstester for rene logikkfunksjoner i app.js.
 * Tester parseUserId() og validateForm() isolert,
 * uten avhengighet til nettleser, API eller database.
 *
 * Kjør med: npm test
 */

// ---------------------------------------------------------------------------
// Mock av DOM-miljø
// ---------------------------------------------------------------------------
// validateForm() leser verdier fra DOM-elementer. Vi lager minimale
// mock-elementer slik at funksjonen kan kjøres uten en ekte nettleser.

function setupDOM({ inquiry = "", shortDesc = "", longDesc = "" } = {}) {
  document.body.innerHTML = `
    <select id="inquiry"><option value="${inquiry}">${inquiry}</option></select>
    <input id="Description" value="${shortDesc}" />
    <textarea id="LongDescription">${longDesc}</textarea>
    <span id="inquiryError"></span>
    <span id="DescriptionError"></span>
    <span id="LongDescriptionError"></span>
  `;
  document.getElementById("inquiry").value = inquiry;
}

const { parseUserId, validateForm } = require("./app");

// ===========================================================================
// parseUserId
// ===========================================================================

describe("parseUserId", () => {

  describe("gyldige brukernavn", () => {
    test("user1 returnerer 1", () => {
      expect(parseUserId("user1")).toBe(1);
    });

    test("user99 returnerer 99", () => {
      expect(parseUserId("user99")).toBe(99);
    });

    test("user10 returnerer 10", () => {
      expect(parseUserId("user10")).toBe(10);
    });

    test("store bokstaver (USER1) returnerer 1", () => {
      expect(parseUserId("USER1")).toBe(1);
    });

    test("mellomrom rundt navn trimmes bort", () => {
      expect(parseUserId("  user5  ")).toBe(5);
    });
  });

  describe("ugyldige brukernavn", () => {
    test("user0 returnerer null (under grense)", () => {
      expect(parseUserId("user0")).toBeNull();
    });

    test("user100 returnerer null (over grense)", () => {
      expect(parseUserId("user100")).toBeNull();
    });

    test("tomt streng returnerer null", () => {
      expect(parseUserId("")).toBeNull();
    });

    test("kun tekst uten tall returnerer null", () => {
      expect(parseUserId("bruker")).toBeNull();
    });

    test("feil format (vetle1) returnerer null", () => {
      expect(parseUserId("vetle1")).toBeNull();
    });

    test("tall uten prefix returnerer null", () => {
      expect(parseUserId("5")).toBeNull();
    });

    test("user med spesialtegn returnerer null", () => {
      expect(parseUserId("user1!")).toBeNull();
    });
  });

});

// ===========================================================================
// validateForm
// ===========================================================================

describe("validateForm", () => {

  test("returnerer true når alle felt er fylt ut", () => {
    setupDOM({
      inquiry: "Nettverksproblem",
      shortDesc: "Kan ikke koble til VPN",
      longDesc: "VPN sluttet å fungere etter oppdatering i dag morges."
    });
    expect(validateForm()).toBe(true);
  });

  test("returnerer false når inquiry ikke er valgt", () => {
    setupDOM({
      inquiry: "-- Velg --",
      shortDesc: "Kort beskrivelse",
      longDesc: "Detaljert beskrivelse"
    });
    expect(validateForm()).toBe(false);
  });

  test("returnerer false når inquiry er tom", () => {
    setupDOM({
      inquiry: "",
      shortDesc: "Kort beskrivelse",
      longDesc: "Detaljert beskrivelse"
    });
    expect(validateForm()).toBe(false);
  });

  test("returnerer false når kort beskrivelse mangler", () => {
    setupDOM({
      inquiry: "Hardware",
      shortDesc: "",
      longDesc: "Detaljert beskrivelse"
    });
    expect(validateForm()).toBe(false);
  });

  test("returnerer false når detaljert beskrivelse mangler", () => {
    setupDOM({
      inquiry: "Hardware",
      shortDesc: "Kort beskrivelse",
      longDesc: ""
    });
    expect(validateForm()).toBe(false);
  });

  test("returnerer false når alle felt mangler", () => {
    setupDOM({ inquiry: "", shortDesc: "", longDesc: "" });
    expect(validateForm()).toBe(false);
  });

  test("setter feilmelding på inquiry når den mangler", () => {
    setupDOM({
      inquiry: "-- Velg --",
      shortDesc: "Test",
      longDesc: "Test"
    });
    validateForm();
    const error = document.getElementById("inquiryError").textContent;
    expect(error).not.toBe("");
  });

  test("setter feilmelding på kort beskrivelse når den mangler", () => {
    setupDOM({
      inquiry: "Software",
      shortDesc: "",
      longDesc: "Detaljert beskrivelse"
    });
    validateForm();
    const error = document.getElementById("DescriptionError").textContent;
    expect(error).not.toBe("");
  });

  test("setter feilmelding på detaljert beskrivelse når den mangler", () => {
    setupDOM({
      inquiry: "Software",
      shortDesc: "Kort beskrivelse",
      longDesc: ""
    });
    validateForm();
    const error = document.getElementById("LongDescriptionError").textContent;
    expect(error).not.toBe("");
  });

  test("ingen feilmeldinger når skjema er gyldig", () => {
    setupDOM({
      inquiry: "Software",
      shortDesc: "Kort beskrivelse",
      longDesc: "Detaljert beskrivelse"
    });
    validateForm();
    expect(document.getElementById("inquiryError").textContent).toBe("");
    expect(document.getElementById("DescriptionError").textContent).toBe("");
    expect(document.getElementById("LongDescriptionError").textContent).toBe("");
  });

});