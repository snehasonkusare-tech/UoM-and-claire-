"""Transform raw DoHS v3 JSON pages into useful organisation and detail CSVs."""

import csv
import json
from contextlib import ExitStack
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent
RAW_DIR = DATA_DIR / "raw" / "directory_of_healthcare_services_v3"
ORGANISATIONS_FILE = DATA_DIR / "processed" / "dohs_organisations.csv"
FULL_DATA_DIR = DATA_DIR / "processed" / "dohs_full_data"

BASE_COLUMNS = ["SourceFile", "SearchKey", "ODSCode", "OrganisationName"]

METRIC_COLUMNS = [
    "MetricID", "MetricName", "DisplayName", "Description", "Value", "Value2",
    "Value3", "Text", "LinkUrl", "LinkText", "MetricDisplayTypeID",
    "MetricDisplayTypeName", "HospitalSectorType", "MetricText", "DefaultText",
    "IsMetaMetric", "BandingClassification", "BandingName",
]

LAST_UPDATED_COLUMNS = [
    "OpeningTimes", "BankHolidayOpeningTimes", "TemporaryChangesOpeningTimes",
    "DentistsAcceptingPatients", "Facilities", "HospitalDepartment", "Services",
    "ContactDetails", "AcceptingPatients", "ServiceOpeningTimes", "GpRegistration",
    "OutpatientAppointments", "TrustServices",
]

# One row per organisation. Repeating collections are summarised here and retained
# in full detail in the related CSV files below.
ORGANISATION_COLUMNS = [
    "SourceFile", "SearchScore", "SearchKey", "ODSCode", "OrganisationName",
    "OrganisationTypeId", "OrganisationType", "OrganisationSubType",
    "OrganisationStatus", "Address1", "Address2", "Address3", "City", "County",
    "Postcode", "Country", "Latitude", "Longitude", "GeocodeType",
    "GeocodeCoordinates", "GeocodeCRSType", "GeocodeCRSName", "IsEpsEnabled",
    "ParentODSCode", "ParentOrganisationName", "Aliases", "AliasesDetailed",
    "TelephoneNumbers", "EmailAddresses", "Websites", "OtherContacts",
    "ContactsDetailed", "Services", "ServiceProviders", "ServiceContacts",
    "ServiceTreatments", "ServiceAgeRanges", "ServiceSpecificOpeningTimes",
    "ServiceMetrics", "ServiceKeyValueData", "Facilities", "FacilitiesDetailed",
    "OpeningTimes", "Metrics", "TrustODSCodeOrName", "TrustsDetailed",
    "RelatedIAPTCCGCodeOrName", "RelatedIAPTCCGsDetailed", "GPRegistrationLink",
    "AcceptingOutOfArea", "AcceptingPatientsGP", "AcceptingPatientsDentist",
    "GPEDecAcceptingOutOfArea", "GPEDecLastUpdatedDate",
    "GPPracticeAcceptingPatients", "GPPracticeAcceptingOutOfArea",
    "GPPracticeLastUpdatedDate", "GSDDataSuppliers", "GSDDataSuppliersDetailed",
    "GSDServices", "GSDServicesDetailed", "GSDMetricTitles",
    "ServiceOpeningPeriods", "LastUpdatedOpeningTimes",
    "LastUpdatedBankHolidayOpeningTimes", "LastUpdatedTemporaryChangesOpeningTimes",
    "LastUpdatedDentistsAcceptingPatients", "LastUpdatedFacilities",
    "LastUpdatedHospitalDepartment", "LastUpdatedServices",
    "LastUpdatedContactDetails", "LastUpdatedAcceptingPatients",
    "LastUpdatedServiceOpeningTimes", "LastUpdatedGpRegistration",
    "LastUpdatedOutpatientAppointments", "LastUpdatedTrustServices",
    "LastUpdatedKeyValueData", "AcceptingPatientsDataPresent",
    "GPRegistrationDataPresent", "GSDProfilePresent", "GPEDecDataPresent",
    "GPPracticeDataPresent", "LastUpdatedDatesPresent", "AliasCount", "ContactCount",
    "ServiceCount", "ServiceProviderCount", "ServiceContactCount",
    "ServiceTreatmentCount", "ServiceAgeRangeCount", "ServiceSpecificOpeningTimeCount",
    "ServiceMetricCount", "ServiceKeyValueDataCount", "FacilityCount",
    "OpeningTimeCount", "MetricCount", "TrustCount", "RelatedIAPTCCGCount",
    "ServiceOpeningTimeCount", "GSDServiceCount", "GSDMetricCount",
    "GSDDataSupplierCount", "DentistAcceptanceCount",
]

TABLE_COLUMNS = {
    "organisation_aliases.csv": BASE_COLUMNS + [
        "AliasIndex", "OrganisationAliasId", "OrganisationAlias"
    ],
    "organisation_contacts.csv": BASE_COLUMNS + [
        "ContactIndex", "ContactType", "ContactAvailabilityType",
        "ContactMethodType", "ContactValue"
    ],
    "organisation_facilities.csv": BASE_COLUMNS + [
        "FacilityIndex", "Id", "Name", "Value", "FacilityGroupName"
    ],
    "organisation_opening_times.csv": BASE_COLUMNS + [
        "OpeningTimeIndex", "Weekday", "OpeningTime", "ClosingTime",
        "OffsetOpeningTime", "OffsetClosingTime", "OpeningTimeType",
        "AdditionalOpeningDate", "IsOpen"
    ],
    "organisation_metrics.csv": BASE_COLUMNS + ["MetricIndex"] + METRIC_COLUMNS,
    "accepting_patients_dentists.csv": BASE_COLUMNS + [
        "AcceptanceIndex", "Id", "Name", "AcceptingPatients"
    ],
    "gsd_data_suppliers.csv": BASE_COLUMNS + [
        "SupplierIndex", "ProvidedBy", "ProvidedByImage", "ProvidedByUrl", "ProvidedOn"
    ],
    "gsd_services.csv": BASE_COLUMNS + [
        "GSDServiceIndex", "ServiceId", "ServiceName"
    ],
    "gsd_metrics.csv": BASE_COLUMNS + [
        "GSDMetricIndex", "ElementTitle", "ElementText", "ElementOrder", "MetricId"
    ],
    "services.csv": BASE_COLUMNS + [
        "ServiceIndex", "ServiceCode", "ServiceName", "ServiceProviderODSCode",
        "ServiceProviderOrganisationName", "ContactCount", "TreatmentCount",
        "OpeningTimeCount", "AgeRangeCount", "MetricCount", "KeyValueDataCount"
    ],
    "service_contacts.csv": BASE_COLUMNS + [
        "ServiceIndex", "ServiceCode", "ServiceName", "ContactIndex",
        "ContactMethodType", "ContactValue"
    ],
    "service_providers.csv": BASE_COLUMNS + [
        "ServiceIndex", "ServiceCode", "ServiceName", "ProviderODSCode",
        "ProviderOrganisationName"
    ],
    "service_treatments.csv": BASE_COLUMNS + [
        "ServiceIndex", "ServiceCode", "ServiceName", "TreatmentIndex", "Name"
    ],
    "service_age_ranges.csv": BASE_COLUMNS + [
        "ServiceIndex", "ServiceCode", "ServiceName", "AgeRangeIndex",
        "FromAgeDays", "ToAgeDays"
    ],
    "service_opening_times.csv": BASE_COLUMNS + [
        "ServiceIndex", "ServiceCode", "ServiceName", "OpeningTimeIndex", "Weekday",
        "OpeningTime", "ClosingTime", "OffsetOpeningTime", "OffsetClosingTime",
        "OpeningTimeType", "AdditionalOpeningDate", "IsOpen", "FromAgeDays", "ToAgeDays"
    ],
    "service_metrics.csv": BASE_COLUMNS + [
        "ServiceIndex", "ServiceCode", "ServiceName", "MetricIndex"
    ] + METRIC_COLUMNS,
    "service_key_value_data.csv": BASE_COLUMNS + [
        "ServiceIndex", "ServiceCode", "ServiceName", "KeyValueIndex", "Key", "Value"
    ],
    "service_opening_periods.csv": BASE_COLUMNS + [
        "PeriodIndex", "Id", "Name", "TypeId", "StartDate", "EndDate",
        "ServiceCount", "DailyOpeningTimeCount"
    ],
    "service_opening_period_services.csv": BASE_COLUMNS + [
        "PeriodIndex", "PeriodServiceIndex", "ServiceCode", "ServiceName"
    ],
    "service_opening_period_daily_times.csv": BASE_COLUMNS + [
        "PeriodIndex", "DailyOpeningTimeIndex", "OpeningTimeIndex", "Weekday",
        "OpeningTime", "ClosingTime", "OffsetOpeningTime", "OffsetClosingTime"
    ],
    "trusts.csv": BASE_COLUMNS + ["TrustIndex", "TrustODSCode", "TrustOrganisationName"],
    "related_iapt_ccgs.csv": BASE_COLUMNS + [
        "RelatedCCGIndex", "RelatedCCGODSCode", "RelatedCCGOrganisationName"
    ],
    "last_updated_dates.csv": BASE_COLUMNS + LAST_UPDATED_COLUMNS,
    "last_updated_key_value_data.csv": BASE_COLUMNS + [
        "UpdateIndex", "ServiceCode", "Key", "LastUpdatedDate"
    ],
}

DATA_DICTIONARY = {
    "dohs_organisations.csv": "One row per DoHS organisation; scalar fields, summaries and counts.",
    "organisation_aliases.csv": "One row per organisation alias.",
    "organisation_contacts.csv": "One row per organisation-level contact method.",
    "organisation_facilities.csv": "One row per organisation facility entry.",
    "organisation_opening_times.csv": "One row per organisation-level opening-time entry.",
    "organisation_metrics.csv": "One row per organisation-level metric.",
    "accepting_patients_dentists.csv": "One row per dentist patient-acceptance category.",
    "gsd_data_suppliers.csv": "One row per supplier of a Generic Directory of Services profile.",
    "gsd_services.csv": "One row per legacy GSD service ID and name.",
    "gsd_metrics.csv": "One row per descriptive GSD profile element.",
    "services.csv": "One row per top-level service offered by an organisation.",
    "service_contacts.csv": "One row per contact attached to a service.",
    "service_providers.csv": "One row per provider attached to a service.",
    "service_treatments.csv": "One row per treatment attached to a service.",
    "service_age_ranges.csv": "One row per age range attached to a service.",
    "service_opening_times.csv": "One row per opening-time entry attached to a service.",
    "service_metrics.csv": "One row per metric attached to a service.",
    "service_key_value_data.csv": "One row per service-specific key/value item.",
    "service_opening_periods.csv": "One row per dated service-opening period.",
    "service_opening_period_services.csv": "One row per service assigned to an opening period.",
    "service_opening_period_daily_times.csv": "One row per daily time inside an opening period.",
    "trusts.csv": "One row per trust related to an organisation.",
    "related_iapt_ccgs.csv": "One row per IAPT CCG related to an organisation.",
    "last_updated_dates.csv": "One row per organisation containing field-level update dates.",
    "last_updated_key_value_data.csv": "One row per update date for a service key/value item.",
}


def text(value):
    """Convert a value to safe CSV text while preserving zero and false values."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def join_unique(values):
    """Join non-empty values once, keeping their source order."""
    result = []
    seen = set()
    for value in values:
        value = text(value).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return " | ".join(result)


def join_preview(values, detail_file, limit=30000):
    """Keep a summary cell Excel-friendly; full rows remain in the detail CSV."""
    joined = join_unique(values)
    if len(joined) <= limit:
        return joined
    shortened = joined[:limit].rsplit(" | ", 1)[0]
    return f"{shortened} | [More rows in {detail_file}]"


def labelled(code, name):
    code, name = text(code), text(name)
    return f"{code}: {name}" if code and name else code or name


def contact_summary(contacts):
    values = []
    for contact in contacts:
        labels = [
            text(contact.get("ContactType")),
            text(contact.get("ContactAvailabilityType")),
            text(contact.get("ContactMethodType")),
        ]
        prefix = " / ".join(value for value in labels if value)
        value = text(contact.get("ContactValue"))
        values.append(f"{prefix}: {value}" if prefix and value else prefix or value)
    return join_unique(values)


def contact_values(contacts, method):
    return join_unique(
        contact.get("ContactValue")
        for contact in contacts
        if contact.get("ContactMethodType") == method
    )


def opening_time_label(opening):
    kind = text(opening.get("OpeningTimeType"))
    day = text(opening.get("Weekday")) or text(opening.get("AdditionalOpeningDate"))
    start, end = text(opening.get("OpeningTime")), text(opening.get("ClosingTime"))
    if start or end:
        hours = f"{start}-{end}"
    elif opening.get("IsOpen") is True:
        hours = "Open"
    elif opening.get("IsOpen") is False:
        hours = "Closed"
    else:
        hours = ""
    return " ".join(part for part in (kind, day, hours) if part)


def metric_label(metric):
    name = text(metric.get("DisplayName")) or text(metric.get("MetricName"))
    value = text(metric.get("Text")) or text(metric.get("BandingName")) or text(metric.get("Value"))
    return f"{name}: {value}" if name and value else name or value


def service_child_summary(services, child_name, formatter):
    values = []
    for service in services:
        service_name = labelled(service.get("ServiceCode"), service.get("ServiceName"))
        for child in service.get(child_name) or []:
            child_value = formatter(child)
            values.append(f"{service_name} -> {child_value}" if service_name else child_value)
    return join_unique(values)


def make_organisation_row(record, source_file):
    contacts = record.get("Contacts") or []
    services = record.get("Services") or []
    facilities = record.get("Facilities") or []
    opening_times = record.get("OpeningTimes") or []
    metrics = record.get("Metrics") or []
    aliases = record.get("OrganisationAliases") or []
    trusts = record.get("Trusts") or []
    related_ccgs = record.get("RelatedIAPTCCGs") or []
    periods = record.get("ServiceOpeningTimes") or []
    accepting = record.get("AcceptingPatients") or {}
    dentist_acceptance = accepting.get("Dentist") or []
    registration = record.get("GPRegistration") or {}
    gsd = record.get("GSD") or {}
    gp_accepting = record.get("GpAcceptingPatients") or {}
    gp_edec = gp_accepting.get("EDec") or {}
    gp_practice = gp_accepting.get("Practice") or {}
    updated = record.get("LastUpdatedDates") or {}
    geocode = record.get("Geocode") or {}
    crs = geocode.get("crs") or {}
    parent = record.get("ParentOrganisation") or {}

    service_providers = [s.get("ServiceProvider") for s in services if s.get("ServiceProvider")]
    gsd_suppliers = gsd.get("DataSupplier") or []
    gsd_services = gsd.get("GsdServices") or []
    gsd_metrics = gsd.get("Metrics") or []
    service_contact_count = sum(len(s.get("Contacts") or []) for s in services)
    service_treatment_count = sum(len(s.get("Treatments") or []) for s in services)
    service_age_count = sum(len(s.get("AgeRange") or []) for s in services)
    service_opening_count = sum(len(s.get("OpeningTimes") or []) for s in services)
    service_metric_count = sum(len(s.get("Metrics") or []) for s in services)
    service_key_value_count = sum(len(s.get("KeyValueData") or []) for s in services)

    row = {
        "SourceFile": source_file, "SearchScore": record.get("@search.score"),
        "SearchKey": record.get("SearchKey"), "ODSCode": record.get("ODSCode"),
        "OrganisationName": record.get("OrganisationName"),
        "OrganisationTypeId": record.get("OrganisationTypeId"),
        "OrganisationType": record.get("OrganisationType"),
        "OrganisationSubType": record.get("OrganisationSubType"),
        "OrganisationStatus": record.get("OrganisationStatus"),
        "Address1": record.get("Address1"), "Address2": record.get("Address2"),
        "Address3": record.get("Address3"), "City": record.get("City"),
        "County": record.get("County"), "Postcode": record.get("Postcode"),
        "Country": record.get("Country"), "Latitude": record.get("Latitude"),
        "Longitude": record.get("Longitude"), "IsEpsEnabled": record.get("IsEpsEnabled"),
        "GeocodeType": geocode.get("type"), "GeocodeCoordinates": geocode.get("coordinates"),
        "GeocodeCRSType": crs.get("type"),
        "GeocodeCRSName": (crs.get("properties") or {}).get("name"),
        "ParentODSCode": parent.get("ODSCode"),
        "ParentOrganisationName": parent.get("OrganisationName"),
        "Aliases": join_unique(a.get("OrganisationAlias") for a in aliases),
        "AliasesDetailed": join_unique(labelled(a.get("OrganisationAliasId"), a.get("OrganisationAlias")) for a in aliases),
        "TelephoneNumbers": contact_values(contacts, "Telephone"),
        "EmailAddresses": contact_values(contacts, "Email"),
        "Websites": contact_values(contacts, "Website"),
        "OtherContacts": contact_values(contacts, "Mixed"),
        "ContactsDetailed": contact_summary(contacts),
        "Services": join_unique(labelled(s.get("ServiceCode"), s.get("ServiceName")) for s in services),
        "ServiceProviders": join_unique(labelled(p.get("ODSCode"), p.get("OrganisationName")) for p in service_providers),
        "ServiceContacts": service_child_summary(services, "Contacts", lambda x: labelled(x.get("ContactMethodType"), x.get("ContactValue"))),
        "ServiceTreatments": service_child_summary(services, "Treatments", lambda x: x.get("Name")),
        "ServiceAgeRanges": service_child_summary(services, "AgeRange", lambda x: f"{text(x.get('FromAgeDays'))}-{text(x.get('ToAgeDays'))} days"),
        "ServiceSpecificOpeningTimes": service_child_summary(services, "OpeningTimes", opening_time_label),
        "ServiceMetrics": service_child_summary(services, "Metrics", metric_label),
        "ServiceKeyValueData": service_child_summary(services, "KeyValueData", lambda x: labelled(x.get("Key"), x.get("Value"))),
        "Facilities": join_unique(f"{text(f.get('FacilityGroupName'))}: {text(f.get('Name'))}={text(f.get('Value'))}" for f in facilities),
        "FacilitiesDetailed": join_unique(f"{text(f.get('Id'))}: {text(f.get('FacilityGroupName'))}: {text(f.get('Name'))}={text(f.get('Value'))}" for f in facilities),
        "OpeningTimes": join_unique(opening_time_label(o) for o in opening_times),
        "Metrics": join_unique(metric_label(m) for m in metrics),
        "TrustODSCodeOrName": join_unique(t.get("ODSCode") or t.get("OrganisationName") for t in trusts),
        "TrustsDetailed": join_preview(
            (labelled(t.get("ODSCode"), t.get("OrganisationName")) for t in trusts),
            "dohs_full_data/trusts.csv",
        ),
        "RelatedIAPTCCGCodeOrName": join_unique(c.get("ODSCode") or c.get("OrganisationName") for c in related_ccgs),
        "RelatedIAPTCCGsDetailed": join_unique(labelled(c.get("ODSCode"), c.get("OrganisationName")) for c in related_ccgs),
        "GPRegistrationLink": registration.get("RegistrationLink"),
        "AcceptingOutOfArea": registration.get("AcceptingOutOfArea"),
        "AcceptingPatientsGP": accepting.get("GP"),
        "AcceptingPatientsDentist": dentist_acceptance,
        "GPEDecAcceptingOutOfArea": gp_edec.get("AcceptingOutOfArea"),
        "GPEDecLastUpdatedDate": gp_edec.get("LastUpdatedDate"),
        "GPPracticeAcceptingPatients": gp_practice.get("AcceptingPatients"),
        "GPPracticeAcceptingOutOfArea": gp_practice.get("AcceptingOutOfArea"),
        "GPPracticeLastUpdatedDate": gp_practice.get("LastUpdatedDate"),
        "GSDDataSuppliers": join_unique(s.get("ProvidedBy") for s in gsd_suppliers),
        "GSDDataSuppliersDetailed": join_unique(f"{text(s.get('ProvidedBy'))}; {text(s.get('ProvidedByUrl'))}; {text(s.get('ProvidedOn'))}; {text(s.get('ProvidedByImage'))}" for s in gsd_suppliers),
        "GSDServices": join_unique(s.get("ServiceName") for s in gsd_services),
        "GSDServicesDetailed": join_unique(labelled(s.get("ServiceId"), s.get("ServiceName")) for s in gsd_services),
        "GSDMetricTitles": join_unique(m.get("ElementTitle") for m in gsd_metrics),
        "ServiceOpeningPeriods": join_unique(f"{text(p.get('Name'))}: {text(p.get('StartDate'))} to {text(p.get('EndDate'))}" for p in periods),
        "LastUpdatedOpeningTimes": updated.get("OpeningTimes"),
        "LastUpdatedBankHolidayOpeningTimes": updated.get("BankHolidayOpeningTimes"),
        "LastUpdatedTemporaryChangesOpeningTimes": updated.get("TemporaryChangesOpeningTimes"),
        "LastUpdatedDentistsAcceptingPatients": updated.get("DentistsAcceptingPatients"),
        "LastUpdatedFacilities": updated.get("Facilities"),
        "LastUpdatedHospitalDepartment": updated.get("HospitalDepartment"),
        "LastUpdatedServices": updated.get("Services"),
        "LastUpdatedContactDetails": updated.get("ContactDetails"),
        "LastUpdatedAcceptingPatients": updated.get("AcceptingPatients"),
        "LastUpdatedServiceOpeningTimes": updated.get("ServiceOpeningTimes"),
        "LastUpdatedGpRegistration": updated.get("GpRegistration"),
        "LastUpdatedOutpatientAppointments": updated.get("OutpatientAppointments"),
        "LastUpdatedTrustServices": updated.get("TrustServices"),
        "LastUpdatedKeyValueData": join_unique(f"{text(x.get('ServiceCode'))}/{text(x.get('Key'))}: {text(x.get('LastUpdatedDate'))}" for x in (updated.get("KeyValueData") or [])),
        "AcceptingPatientsDataPresent": record.get("AcceptingPatients") is not None,
        "GPRegistrationDataPresent": record.get("GPRegistration") is not None,
        "GSDProfilePresent": record.get("GSD") is not None,
        "GPEDecDataPresent": gp_accepting.get("EDec") is not None,
        "GPPracticeDataPresent": gp_accepting.get("Practice") is not None,
        "LastUpdatedDatesPresent": record.get("LastUpdatedDates") is not None,
        "AliasCount": len(aliases), "ContactCount": len(contacts),
        "ServiceCount": len(services), "ServiceProviderCount": len(service_providers),
        "ServiceContactCount": service_contact_count,
        "ServiceTreatmentCount": service_treatment_count,
        "ServiceAgeRangeCount": service_age_count,
        "ServiceSpecificOpeningTimeCount": service_opening_count,
        "ServiceMetricCount": service_metric_count,
        "ServiceKeyValueDataCount": service_key_value_count,
        "FacilityCount": len(facilities), "OpeningTimeCount": len(opening_times),
        "MetricCount": len(metrics), "TrustCount": len(trusts),
        "RelatedIAPTCCGCount": len(related_ccgs), "ServiceOpeningTimeCount": len(periods),
        "GSDServiceCount": len(gsd_services), "GSDMetricCount": len(gsd_metrics),
        "GSDDataSupplierCount": len(gsd_suppliers),
        "DentistAcceptanceCount": len(dentist_acceptance),
    }
    return {column: text(row.get(column)) for column in ORGANISATION_COLUMNS}


def base_row(record, source_file):
    return {
        "SourceFile": source_file, "SearchKey": record.get("SearchKey"),
        "ODSCode": record.get("ODSCode"), "OrganisationName": record.get("OrganisationName"),
    }


def write_detail_rows(record, source_file, writers, counts):
    """Write repeating JSON collections to correctly grained CSV files."""
    base = base_row(record, source_file)

    def write(file_name, values):
        writers[file_name].writerow({k: text(v) for k, v in {**base, **values}.items()})
        counts[file_name] += 1

    for index, alias in enumerate(record.get("OrganisationAliases") or [], 1):
        write("organisation_aliases.csv", {"AliasIndex": index, **alias})
    for index, contact in enumerate(record.get("Contacts") or [], 1):
        write("organisation_contacts.csv", {"ContactIndex": index, **contact})
    for index, facility in enumerate(record.get("Facilities") or [], 1):
        write("organisation_facilities.csv", {"FacilityIndex": index, **facility})
    for index, opening in enumerate(record.get("OpeningTimes") or [], 1):
        write("organisation_opening_times.csv", {"OpeningTimeIndex": index, **opening})
    for index, metric in enumerate(record.get("Metrics") or [], 1):
        write("organisation_metrics.csv", {"MetricIndex": index, **metric})

    accepting = record.get("AcceptingPatients") or {}
    for index, entry in enumerate(accepting.get("Dentist") or [], 1):
        write("accepting_patients_dentists.csv", {"AcceptanceIndex": index, **entry})

    gsd = record.get("GSD") or {}
    for index, supplier in enumerate(gsd.get("DataSupplier") or [], 1):
        write("gsd_data_suppliers.csv", {"SupplierIndex": index, **supplier})
    for index, service in enumerate(gsd.get("GsdServices") or [], 1):
        write("gsd_services.csv", {"GSDServiceIndex": index, **service})
    for index, metric in enumerate(gsd.get("Metrics") or [], 1):
        write("gsd_metrics.csv", {"GSDMetricIndex": index, **metric})

    for service_index, service in enumerate(record.get("Services") or [], 1):
        service_id = {
            "ServiceIndex": service_index, "ServiceCode": service.get("ServiceCode"),
            "ServiceName": service.get("ServiceName"),
        }
        provider = service.get("ServiceProvider") or {}
        write("services.csv", {
            **service_id, "ServiceProviderODSCode": provider.get("ODSCode"),
            "ServiceProviderOrganisationName": provider.get("OrganisationName"),
            "ContactCount": len(service.get("Contacts") or []),
            "TreatmentCount": len(service.get("Treatments") or []),
            "OpeningTimeCount": len(service.get("OpeningTimes") or []),
            "AgeRangeCount": len(service.get("AgeRange") or []),
            "MetricCount": len(service.get("Metrics") or []),
            "KeyValueDataCount": len(service.get("KeyValueData") or []),
        })
        if service.get("ServiceProvider") is not None:
            write("service_providers.csv", {
                **service_id, "ProviderODSCode": provider.get("ODSCode"),
                "ProviderOrganisationName": provider.get("OrganisationName"),
            })
        for index, contact in enumerate(service.get("Contacts") or [], 1):
            write("service_contacts.csv", {**service_id, "ContactIndex": index, **contact})
        for index, treatment in enumerate(service.get("Treatments") or [], 1):
            write("service_treatments.csv", {**service_id, "TreatmentIndex": index, **treatment})
        for index, age in enumerate(service.get("AgeRange") or [], 1):
            write("service_age_ranges.csv", {**service_id, "AgeRangeIndex": index, **age})
        for index, opening in enumerate(service.get("OpeningTimes") or [], 1):
            write("service_opening_times.csv", {**service_id, "OpeningTimeIndex": index, **opening})
        for index, metric in enumerate(service.get("Metrics") or [], 1):
            write("service_metrics.csv", {**service_id, "MetricIndex": index, **metric})
        for index, item in enumerate(service.get("KeyValueData") or [], 1):
            write("service_key_value_data.csv", {**service_id, "KeyValueIndex": index, **item})

    for period_index, period in enumerate(record.get("ServiceOpeningTimes") or [], 1):
        daily_times = period.get("DailyOpeningTimes") or []
        write("service_opening_periods.csv", {
            "PeriodIndex": period_index, "Id": period.get("Id"), "Name": period.get("Name"),
            "TypeId": period.get("TypeId"), "StartDate": period.get("StartDate"),
            "EndDate": period.get("EndDate"), "ServiceCount": len(period.get("Services") or []),
            "DailyOpeningTimeCount": len(daily_times),
        })
        for index, service in enumerate(period.get("Services") or [], 1):
            write("service_opening_period_services.csv", {
                "PeriodIndex": period_index, "PeriodServiceIndex": index, **service
            })
        for daily_index, daily in enumerate(daily_times, 1):
            for opening_index, opening in enumerate(daily.get("OpeningTimes") or [], 1):
                write("service_opening_period_daily_times.csv", {
                    "PeriodIndex": period_index, "DailyOpeningTimeIndex": daily_index,
                    "OpeningTimeIndex": opening_index, "Weekday": daily.get("Weekday"), **opening,
                })

    for index, trust in enumerate(record.get("Trusts") or [], 1):
        write("trusts.csv", {
            "TrustIndex": index, "TrustODSCode": trust.get("ODSCode"),
            "TrustOrganisationName": trust.get("OrganisationName"),
        })
    for index, ccg in enumerate(record.get("RelatedIAPTCCGs") or [], 1):
        write("related_iapt_ccgs.csv", {
            "RelatedCCGIndex": index, "RelatedCCGODSCode": ccg.get("ODSCode"),
            "RelatedCCGOrganisationName": ccg.get("OrganisationName"),
        })

    updated = record.get("LastUpdatedDates")
    if updated is not None:
        write("last_updated_dates.csv", {key: updated.get(key) for key in LAST_UPDATED_COLUMNS})
        for index, item in enumerate(updated.get("KeyValueData") or [], 1):
            write("last_updated_key_value_data.csv", {"UpdateIndex": index, **item})


def write_data_dictionary():
    path = FULL_DATA_DIR / "data_dictionary.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["FileName", "RowMeaning", "JoinKey", "Notes"])
        writer.writeheader()
        for file_name, meaning in DATA_DICTIONARY.items():
            writer.writerow({
                "FileName": file_name, "RowMeaning": meaning, "JoinKey": "SearchKey",
                "Notes": "Index columns preserve source-array order within each organisation or service.",
            })


def main():
    page_files = sorted(RAW_DIR.glob("page_*.json"))
    if not page_files:
        raise FileNotFoundError(f"No DoHS page files found in {RAW_DIR}")

    ORGANISATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    FULL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_organisations = ORGANISATIONS_FILE.with_suffix(".csv.tmp")
    counts = {file_name: 0 for file_name in TABLE_COLUMNS}
    seen_search_keys = set()
    organisation_count = 0

    with ExitStack() as stack:
        organisation_file = stack.enter_context(
            temporary_organisations.open("w", encoding="utf-8-sig", newline="")
        )
        organisation_writer = csv.DictWriter(
            organisation_file, fieldnames=ORGANISATION_COLUMNS, extrasaction="ignore"
        )
        organisation_writer.writeheader()

        writers = {}
        for file_name, columns in TABLE_COLUMNS.items():
            file = stack.enter_context(
                (FULL_DATA_DIR / file_name).open("w", encoding="utf-8-sig", newline="")
            )
            writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writers[file_name] = writer

        for page_number, page_file in enumerate(page_files, 1):
            with page_file.open("r", encoding="utf-8") as json_file:
                records = json.load(json_file).get("value", [])
            for record in records:
                search_key = record.get("SearchKey")
                if not search_key:
                    raise ValueError(f"Missing SearchKey in {page_file.name}")
                if search_key in seen_search_keys:
                    raise ValueError(f"Duplicate SearchKey found: {search_key}")
                seen_search_keys.add(search_key)
                organisation_writer.writerow(make_organisation_row(record, page_file.name))
                write_detail_rows(record, page_file.name, writers, counts)
                organisation_count += 1
            if page_number % 10 == 0 or page_number == len(page_files):
                print(f"Processed {page_number}/{len(page_files)} JSON files ({organisation_count:,} organisations)")

    temporary_organisations.replace(ORGANISATIONS_FILE)
    write_data_dictionary()
    print(f"Created: {ORGANISATIONS_FILE}")
    print(f"Created detail folder: {FULL_DATA_DIR}")
    print(f"Organisation rows: {organisation_count:,}")
    for file_name, count in counts.items():
        print(f"  {file_name}: {count:,} rows")


if __name__ == "__main__":
    main()
