import argparse
import os
import sys

import json
import pandas


### Invoice field names
INVOICE_DATE_FIELD = "Invoice Month"
PROJECT_FIELD = "Project - Allocation"
PROJECT_ID_FIELD = "Project - Allocation ID"
PI_FIELD = "Manager (PI)"
INVOICE_EMAIL_FIELD = "Invoice Email"
INVOICE_ADDRESS_FIELD = "Invoice Address"
INSTITUTION_FIELD = "Institution"
INSTITUTION_ID_FIELD = "Institution - Specific Code"
SU_HOURS_FIELD = "SU Hours (GBhr or SUhr)"
SU_TYPE_FIELD = "SU Type"
COST_FIELD = "Cost"
CREDIT_FIELD = "Credit"
CREDIT_CODE_FIELD = "Credit Code"
BALANCE_FIELD = "Balance"
###


def get_institution_from_pi(institute_map, pi_uname):
    institution_key = pi_uname.split("@")[-1]
    institution_name = institute_map.get(institution_key, "")

    if institution_name == "":
        print(f"Warning: PI name {pi_uname} does not match any institution!")

    return institution_name


def load_institute_map() -> dict:
    with open("institute_map.json", "r") as f:
        institute_map = json.load(f)

    return institute_map


def load_old_pis(old_pi_file):
    old_pi_dict = dict()

    try:
        with open(old_pi_file) as f:
            for pi_info in f:
                pi, first_month = pi_info.strip().split(",")
                old_pi_dict[pi] = first_month
    except FileNotFoundError:
        print("Applying credit 0002 failed. Old PI file does not exist")
        sys.exit(1)

    return old_pi_dict


def is_old_pi(old_pi_dict, pi, invoice_month):
    if pi in old_pi_dict and old_pi_dict[pi] != invoice_month:
        return True
    return False


def main():
    """Remove non-billable PIs and projects"""

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "csv_files",
        nargs="+",
        help="One or more CSV files that need to be processed",
    )
    parser.add_argument(
        "--pi-file",
        required=True,
        help="File containing list of PIs that are non-billable",
    )
    parser.add_argument(
        "--projects-file",
        required=True,
        help="File containing list of projects that are non-billable",
    )
    parser.add_argument(
        "--timed-projects-file",
        required=True,
        help="File containing list of projects that are non-billable within a specified duration",
    )
    parser.add_argument(
        "--output-file",
        required=False,
        default="filtered_output.csv",
        help="Name of output file",
    )
    parser.add_argument(
        "--output-folder",
        required=False,
        default="pi_invoices",
        help="Name of output folder containing pi-specific invoice csvs",
    )
    parser.add_argument(
        "--HU-invoice-file",
        required=False,
        default="HU_only.csv",
        help="Name of output csv for HU invoices",
    )
    parser.add_argument(
        "--HU-BU-invoice-file",
        required=False,
        default="HU_BU.csv",
        help="Name of output csv for HU and BU invoices",
    )
    parser.add_argument(
        "--old-pi-file",
        required=False,
        help="Name of csv file listing previously billed PIs",
    )
    args = parser.parse_args()
    merged_dataframe = merge_csv(args.csv_files)

    pi = []
    projects = []
    with open(args.pi_file) as file:
        pi = [line.rstrip() for line in file]
    with open(args.projects_file) as file:
        projects = [line.rstrip() for line in file]

    invoice_date = get_invoice_date(merged_dataframe)
    print("Invoice date: " + str(invoice_date))

    timed_projects_list = timed_projects(args.timed_projects_file, invoice_date)
    print("The following timed-projects will not be billed for this period: ")
    print(timed_projects_list)

    projects = list(set(projects + timed_projects_list))

    merged_dataframe = add_institution(merged_dataframe)
    remove_billables(merged_dataframe, pi, projects, "non_billable.csv")

    billable_projects = remove_non_billables(merged_dataframe, pi, projects)
    billable_projects = validate_pi_names(billable_projects)
    credited_projects = apply_credits_new_pi(billable_projects, args.old_pi_file)
    export_billables(credited_projects, args.output_file)
    export_pi_billables(billable_projects, args.output_folder)
    export_HU_only(billable_projects, args.HU_invoice_file)
    export_HU_BU(billable_projects, args.HU_BU_invoice_file)
    export_lenovo(billable_projects)


def merge_csv(files):
    """Merge multiple CSV files and return a single pandas dataframe"""
    dataframes = []
    for file in files:
        dataframe = pandas.read_csv(file)
        dataframes.append(dataframe)

    merged_dataframe = pandas.concat(dataframes, ignore_index=True)
    merged_dataframe.reset_index(drop=True, inplace=True)
    return merged_dataframe


def get_invoice_date(dataframe):
    """Returns the invoice date as a pandas timestamp object

    Note that it only checks the first entry because it should
    be the same for every row.
    """
    invoice_date_str = dataframe[INVOICE_DATE_FIELD][0]
    invoice_date = pandas.to_datetime(invoice_date_str, format="%Y-%m")
    return invoice_date


def timed_projects(timed_projects_file, invoice_date):
    """Returns list of projects that should be excluded based on dates"""
    dataframe = pandas.read_csv(timed_projects_file)

    # convert to pandas timestamp objects
    dataframe["Start Date"] = pandas.to_datetime(
        dataframe["Start Date"], format="%Y-%m"
    )
    dataframe["End Date"] = pandas.to_datetime(dataframe["End Date"], format="%Y-%m")

    mask = (dataframe["Start Date"] <= invoice_date) & (
        invoice_date <= dataframe["End Date"]
    )
    return dataframe[mask]["Project"].to_list()


def remove_non_billables(dataframe, pi, projects):
    """Removes projects and PIs that should not be billed from the dataframe"""
    filtered_dataframe = dataframe[
        ~dataframe[PI_FIELD].isin(pi) & ~dataframe[PROJECT_FIELD].isin(projects)
    ]
    return filtered_dataframe


def remove_billables(dataframe, pi, projects, output_file):
    """Removes projects and PIs that should be billed from the dataframe

    So this *keeps* the projects/pis that should not be billed.
    """
    filtered_dataframe = dataframe[
        dataframe[PI_FIELD].isin(pi) | dataframe[PROJECT_FIELD].isin(projects)
    ]
    filtered_dataframe.to_csv(output_file, index=False)


def validate_pi_names(dataframe):
    invalid_pi_projects = dataframe[pandas.isna(dataframe[PI_FIELD])]
    for i, row in invalid_pi_projects.iterrows():
        print(f"Warning: Project {row[PROJECT_FIELD]} has empty PI field")
    dataframe = dataframe[~pandas.isna(dataframe[PI_FIELD])]

    return dataframe


def export_billables(dataframe, output_file):
    dataframe.to_csv(output_file, index=False)


def export_pi_billables(dataframe: pandas.DataFrame, output_folder):
    if not os.path.exists(output_folder):
        os.mkdir(output_folder)

    invoice_month = dataframe[INVOICE_DATE_FIELD].iat[0]
    pi_list = dataframe[PI_FIELD].unique()

    for pi in pi_list:
        if pandas.isna(pi):
            continue
        pi_projects = dataframe[dataframe[PI_FIELD] == pi]
        pi_instituition = pi_projects[INSTITUTION_FIELD].iat[0]
        pi_projects.to_csv(
            output_folder + f"/{pi_instituition}_{pi}_{invoice_month}.csv"
        )


def apply_credits_new_pi(dataframe, old_pi_file):
    new_pi_credit_code = "0002"
    new_pi_credit_amount = 1000

    dataframe[CREDIT_FIELD] = None
    dataframe[CREDIT_CODE_FIELD] = None
    dataframe[BALANCE_FIELD] = 0

    old_pi_dict = load_old_pis(old_pi_file)

    current_pi_list = dataframe[PI_FIELD].unique()
    invoice_month = dataframe[INVOICE_DATE_FIELD].iat[0]

    for pi in current_pi_list:
        pi_projects = dataframe[dataframe[PI_FIELD] == pi]

        if is_old_pi(old_pi_dict, pi, invoice_month):
            for i, row in pi_projects.iterrows():
                dataframe.at[i, BALANCE_FIELD] = row[COST_FIELD]
        else:
            remaining_credit = new_pi_credit_amount
            for i, row in pi_projects.iterrows():
                project_cost = row[COST_FIELD]
                applied_credit = min(project_cost, remaining_credit)

                dataframe.at[i, CREDIT_FIELD] = applied_credit
                dataframe.at[i, CREDIT_CODE_FIELD] = new_pi_credit_code
                dataframe.at[i, BALANCE_FIELD] = row[COST_FIELD] - applied_credit
                remaining_credit -= applied_credit

                if remaining_credit == 0:
                    break

    return dataframe


def add_institution(dataframe: pandas.DataFrame):
    """Determine every PI's institution name, logging any PI whose institution cannot be determined
    This is performed by `get_institution_from_pi()`, which tries to match the PI's username to
    a list of known institution email domains (i.e bu.edu), or to several edge cases (i.e rudolph) if
    the username is not an email address.

    Exact matches are then mapped to the corresponding institution name.

    I.e "foo@bu.edu" would match with "bu.edu", which maps to the instition name "Boston University"

    The list of mappings are defined in `institute_map.json`.
    """
    institute_map = load_institute_map()
    for i, row in dataframe.iterrows():
        pi_name = row[PI_FIELD]
        if pandas.isna(pi_name):
            print(f"Project {row[PROJECT_FIELD]} has no PI")
        else:
            dataframe.at[i, INSTITUTION_FIELD] = get_institution_from_pi(
                institute_map, pi_name
            )

    return dataframe


def export_HU_only(dataframe, output_file):
    HU_projects = dataframe[dataframe[INSTITUTION_FIELD] == "Harvard University"]
    HU_projects.to_csv(output_file)


def export_HU_BU(dataframe, output_file):
    HU_BU_projects = dataframe[
        (dataframe[INSTITUTION_FIELD] == "Harvard University")
        | (dataframe[INSTITUTION_FIELD] == "Boston University")
    ]
    HU_BU_projects.to_csv(output_file)


def export_lenovo(dataframe: pandas.DataFrame, output_file=None):
    lenovo_file_name = (
        output_file or f"Lenovo_{dataframe[INVOICE_DATE_FIELD].iat[0]}.csv"
    )

    LENOVO_SU_TYPES = ["OpenShift GPUA100SXM4", "OpenStack GPUA100SXM4"]
    SU_CHARGE_MULTIPLIER = 1

    lenovo_df = dataframe[dataframe[SU_TYPE_FIELD].isin(LENOVO_SU_TYPES)][
        [
            INVOICE_DATE_FIELD,
            PROJECT_FIELD,
            INSTITUTION_FIELD,
            SU_HOURS_FIELD,
            SU_TYPE_FIELD,
        ]
    ]

    lenovo_df.rename(columns={SU_HOURS_FIELD: "SU Hours"}, inplace=True)
    lenovo_df.insert(len(lenovo_df.columns), "SU Charge", SU_CHARGE_MULTIPLIER)
    lenovo_df["Charge"] = lenovo_df["SU Hours"] * lenovo_df["SU Charge"]
    lenovo_df.to_csv(lenovo_file_name)


if __name__ == "__main__":
    main()
