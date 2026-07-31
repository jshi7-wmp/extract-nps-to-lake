#!/usr/bin/env python3
"""
check_headers.py

Validate that a Medallia NPS response CSV export has the columns we expect for
a given brand. Medallia occasionally reorders, renames, adds or drops survey
questions; this catches those changes before the file is uploaded to GCS.

The expected header sets below are compared against the CSV's first (header)
row. Comparison is:
  * whitespace-normalized  (runs of spaces/newlines/tabs collapse to one space)
  * order-independent      (columns may be reordered by Medallia)
  * duplicate-insensitive  (some exports legitimately repeat a column name)

Exit codes:
  0  header matches (or only has extra/new columns -> warning, not fatal)
  1  one or more EXPECTED columns are missing
  2  usage / file / brand error

Usage:
  check_headers.py --brand BWM|EDRS|EDM --file /path/to/export.csv
"""

import argparse
import csv
import re
import sys

# ---------------------------------------------------------------------------
# Expected columns per brand.
# Keep these in sync with the Medallia export layouts. Duplicates are harmless
# (comparison is set-based) but are kept here to mirror the raw export.
# ---------------------------------------------------------------------------
EXPECTED = {
    "BWM": [
        "Response Date",
        "Survey Program",
        "Likelihood to Recommend Score",
        "Survey ID",
        "Transaction ID",
        "Transaction Date - Big W",
        "Delivery Date",
        "Responsedate",
        "Delivery Method",
        "Basket Mix",
        "Seller Name",
        "Seller ID",
        "BIG W Market Order Flag (Y/N)",
        "Overall Satisfaction",
        "Delivered on time",
        "The communication you received on order updates",
        "Completeness of Order",
        "Condition of Packaged Products - Delivery",
        "Delivery Charge Represented Value",
        "Main items were missing",
        "LTR Comment",
        "Main item missing",
        "Final Feedback Comment",
    ],
    "EDRS": [
        "Response Date",
        "Survey Program",
        "Likelihood to Recommend Score",
        "Survey ID",
        "Order ID",
        "Order Date",
        "Dispatch Date",
        "Seller Name",
        "Seller ID",
        "Response Date",
        "Overall, how satisfied or dissatisfied were you with your most recent purchase from the Everyday Rewards Shop?",
        "Navigation / ease of finding products in the Everyday Rewards Shop",
        "Redeeming my Rewards Dollars on products in the Everyday Rewards Shop",
        "Timeliness of order delivery",
        "Communication post purchase",
        "Tracking your order with the delivery partner",
        "Condition and packing of the product",
        "The product meeting your expectations",
        "The cost of having your order delivered",
        "Navigation / ease of the payment process",
        "Have you received all of the items in your Everyday Rewards Shop order?",
        "Everyday Rewards Shop Experience Improve",
        "Something else",
        "What are the reasons you gave this score?",
        "Lastly, do you have any further thoughts you would like to share with us?",
    ],
    "EDM": [
        "Survey ID",
        "Response Date",
        "Transaction Date",
        "CRN",
        "Rewards Card Number",
        "State",
        "Zone",
        "Group",
        "Store",
        "Lifestage",
        "Affluent Segment",
        "Trans Date",
        "Join date",
        "Transaction Time",
        "Time of Day",
        "Checkout Used",
        "Day of Week",
        "NPS Segment",
        "Likelihood to Recommend Score",
        "Overall Satisfaction",
        "Team Attitude",
        "In-store Fruit & Veg",
        "In-store Out of Stocks",
        "Checkout Wait Time",
        "Ease of Pick up",
        "Team member cared",
        "Service Provided by the Team during Pick up",
        "Delivery Driver Cared about the Customers Needs",
        "Team member cared when collecting order",
        "Personal Shopper Cared about Your Needs",
        "Personal Shopper Cared About Needs Delivery Metrics",
        "Fruit & Veg",
        "Meat",
        "Seafood",
        "Bakery",
        "Deli",
        "Liquor",
        "Pharmacy",
        "Ready Meals and Meal Shortcuts",
        "Packaged Health Foods",
        "Completeness of Order",
        "Condition of Packaged Products",
        "Online Fruit & Veg",
        "Range of Products",
        "Woolworths Gift Cards - Overall Satisfaction",
        "Woolworths Gift Card - Payment Experience",
        "Checkout Experience",
        "Ease of Using ACO",
        "Value for Money",
        "Greeted or Acknowledged in Store",
        "Car Parking",
        "Availability of Trolleys and/or Baskets",
        "Ease of Moving Around the Store",
        "Correct Price Tickets",
        "Store Presentation",
        "Store Presentation (Look and Feel)",
        "Display of products makes it easy to shop",
        "Rewards Satisfaction",
        "ROBIS - Website Satisfaction",
        "ROBIS - App Satisfaction",
        "ROBIS - Rewards Website Satisfaction",
        "ROBIS - Rewards App Satisfaction",
        "ROBIS - Other Satisfaction",
        "Communication Received on Order Updates - Pick up",
        "Delivery Charge",
        "Order delivered in time",
        "Communication Received on Order Updates - Delivery",
        "The friendliness & politeness of the delivery driver",
        "Presentation of Delivery Driver",
        "Product substitutions",
        "The order was packed with Care",
        "What are the reasons you gave this rating?[br][br] Please be as detailed as possible, as your responses will be used to improve the service provided by the team.",
        "LTR Comment",
        "Delivery Metrics Comment",
        "Cared Comment",
        "Order Fulfillment Comment",
        "Area where you were Greeted or Acknowledged",
        "Product Substitution Recieved",
        "Meat",
        "Seafood Visited",
        "Bakery Visited",
        "Deli Visited",
        "Ready Meals and Meal Shortcuts Visited",
        "Packaged Health Foods Visited",
        "Departments Visited - None of the Above",
        "Team Member was Rude",
        "Team Member did not Acknowledge",
        "Team Member did not Offer Help",
        "There was no Team Member Available",
        "Team Member was not Knowledgeable",
        "Team Member Cared/Did Not Care Other Reason",
        "Team Member didn't go Above and Beyond",
        "Team Member Tried to Help",
        "Team Member helped but didn't Care",
        "Cared Responses - I didn't interact with anyone because I didn't need or want help",
        "Team Member Acknowledgement",
        "Cared Responses - Team member/s was friendly",
        "Team Member Helpfulness",
        "Team Member went above and beyond",
        "I didn't Interact with Anyone",
        "Team Member Unavailable",
        "I wanted to Interact with Someone, but no one was available",
        "I was in a rush, and just popped in and out of the store",
        "Fruit & Veg Comment",
        "Categories Comment",
        "Out of Stock Comment",
        "Range Comment",
        "General Presentation Comment",
        "Overall Value for Money Comment",
        "Scan and Go Comment - Profanity Free",
        "Research Methods Comment",
        "The ease of navigating the website",
        "The range of products to choose from the website",
        "The products on the website being the same as those in-store",
        "The collection times that were available",
        "The delivery times that were available",
        "The 'track my order' service",
        "The quality and freshness of the fresh food you ordered (including expiry dates)",
        "The website content and information is relevant to me",
        "The app content and information is relevant to me",
        "The ease of navigating the Woolworths Mobile App",
        "The website being fast and reliable",
        "The app being fast and reliable",
        "The ease of checking out and paying for your order",
        "Item(s) that I ordered were missing",
        "Item/s that I ordered were out of stock when I received my order",
        "The substitution(s) I received were not appropriate for me",
        "I specified that I did not want any substitutions",
        "Other Reason",
        "Research - Other - Text",
        "The Pick up location was not convenient",
        "I had to wait more than 5 minutes to receive my order",
        "The Pick up location was hard to find",
        "I found it difficult to find parking",
        "I notified the store that I was on my way, but my order was not ready for Pick up",
        "I was dissatisfied with the service provided by the team",
        "Pick up Reason Other",
        "Pickup Reason",
        "Callback Requested",
        "Preferred Contact Time",
        "Please confirm that you'd like us to contact you on the above details to discuss your feedback",
        "Everyday Program Benefits Score",
        "On behalf of the team at Woolworths, thank you for taking the time to participate and sharing your feedback.If you have a few more minutes, we would love to ask you a few more questions about your order experience. Would you like to continue the survey and answer these questions?",
        "The price of products on the website",
        "The deals and specials available on the website",
        "Ease of finding products and specials",
        "Ease of finding products, relevance and speed of Search bar",
        "Are you aware that Woolworths Online provides customers with the option to receive product substitutions? [i]Substitutions are similar items Woolworths Online selects for you if the original item you have selected is unavailable. [/i]",
        "How satisfied or dissatisfied were you with the product substitution/s you received from your most recent purchase at Woolworths Online?",
        "I know how to select whether or not I want product substitutions in my order",
        "I like that Woolworths Online provides the option to have unavailable products substituted",
        "Harris Farm Online",
        "Coles Online",
        "Woolworths Online",
        "Amazon",
        "Meal Kit deliveries (e.g. Hello Fresh, Marley Spoon...)",
        "Other",
        "IGA",
        "Costco",
        "Aldi",
        "Coles",
        "Harris Farm",
        "IGA/Foodland",
        "Woolworths",
        "Specialist/independent grocers",
        "Other",
        "The price of products on the website",
        "The deals and specials available on the website",
        "The ease of finding products using the Search bar Comment",
        "Ease of finding products and specials Comment",
        "The way your order has been packed Comment",
        "The product substitutions you received Comment",
        "Close Comment",
        "Improve Woolworths Online Service Comment",
        "The collection times that were available Comment",
        "Service our team provided you when collecting your order Comment",
        "The packaged products you received Comment",
        "The time your order was delivered",
        "The friendliness of the delivery driver",
        "The presentation of the delivery driver Comment",
        "The ease of navigating the website Comment",
        "The ease of navigating the Mobile App Comment",
        "The range of products to choose from on the website",
        "The website content being relevant and helpful",
        "The website being fast and reliable",
        "The ease of checking out and paying for your order",
        "The quality and freshness of the fresh food you ordered",
        "Satisfaction with Everyday Program Comment",
        "Overall Pickup Satisfaction Score",
        "I specified that I was happy to receive substitutions, but the items out of stock were not substituted",
        "Team Attitude Comment",
        "Gift Cards OSAT Comment",
        "Checkout Experience Comment",
        "Look and Feel Comment",
        "Rewards Comment",
        "Flex Close - Answer Additional Questions",
        "Availability of Baskets",
        "Heard of Woolworths Scan&Go App",
        "Final Feedback Comment",
        "Order Fulfilment Profanity Free Comment",
        "Checkout Number",
        "Ask Now Author",
        "Ask Now End Date",
        "Ask Now Id",
        "Ask Now Name",
        "Ask Now Question #1",
        "Ask Now Question #1 Wording",
        "Ask Now Question #2",
        "Ask Now Question #2 Wording",
        "Ask Now Question #3",
        "Ask Now Question #3 Wording",
        "Ask Now Question #4",
        "Ask Now Question #4 Wording",
        "Ask Now Question #5",
        "Ask Now Question #5 Wording",
        "Ask Now Start Date",
        "Ask Now Status",
        "Checkout Experience Comment",
        "Out of Stocks Comment",
        "Fulfilment Type",
        "Delivery Unlimited Subscription",
        "LTR Comment",
        "Scan&Go Key Drivers Comment",
        "Scan&Go Drivers Comment",
        "Scan&Go Scan Check Comment",
        "Scan&Go Communication Comment",
        "Store Controllable Comment",
        "Ease of getting set up and started",
        "Ease of using Scan&Go app",
        "Ease of scanning Fruit & Veg",
        "Ease of scanning products with barcodes",
        "Ease of access to receipts",
        "Ease of payment",
        "Length of time taken to checkout",
        "Speed of Getting In and Out",
        "Length of time waiting for assistance at checkout",
        "Being transferred to a register or self checkout to complete your payment",
        "Team Attitude",
        "Satisfaction with the Scan check process",
        "I understand why the Scan Check process is done",
        "The Scan Check process was carried out in a polite and friendly manner",
        "Clearly see that the store has Scan&Go",
        "Scan&Go signage was informative",
        "Scan&Go Hear About",
        "The quality and freshness of the fruit & veg",
        "Products I buy at this store were in stock (the shelf wasn't empty)",
        "Delivery Date",
        "Online Order Number",
        "First Time Customer",
        "Customer Segment Group Name",
        "Customer Segment Name",
        "Customer WOW Comment",
        "Ease of shopping with the Scan&Go list",
        "Vendor",
        "Fulfilment Vendor",
        "New/Renewal status",
        "Overall satisfaction with Everyday Market",
        "Timeliness of order delivery",
        "Communication post purchase",
        "Tracking your order with the delivery partner",
        "Delivery experience",
        "Condition and packing of the product",
        "The product meeting your expectations",
        "The cost of having your order delivered",
        "Everyday Market Experience Improve",
        "How quickly your shopping was handed to you once you arrived",
        "Instructions to collect your order were clear and easy to follow",
        "The driver followed your delivery instructions",
        "The driver placed your bags down with care when delivering your order",
        "Everyday Extra Subscription Flag",
        "Is Only Everyday Market Order",
        "Scan&Go Survey Version",
        "Ease of finding a product using search functions",
        "Ease of tracking my spend",
        "I felt comfortable having a Scan Check",
        "Overall value for money across my total shop",
        "Survey Program",
        "Meat Comment",
        "Which ways have you checked out and paid with Scan&Go Trolley?",
        "How likely are you to use dedicated Scan&Go Trolley exit lane again?",
        "If both the dedicated exit lane and the self checkout are available for Scan&Go Trolley, which would you prefer?",
        "Why did you give this answer?",
        "Have you used Scan&Go Mobile before the introduction of Scan&Go Trolley?",
        "If both Scan&Go Trolley and Scan&Go Mobile were available in the same store, which way would you prefer to shop?",
        "Why would you prefer to shop this way?",
        "How would you rate the range of simple & fresh, quality dinner options at\\nWoolworths",
        "How would you rate Woolworths as convenient for Dinner solutions?",
        "How would you rate WW as your first choice for Dinner solutions?",
    ],
}

_WS = re.compile(r"\s+")


def normalize(name):
    """Collapse whitespace (incl. embedded newlines) and trim, for robust
    comparison against Medallia's slightly variable header formatting."""
    return _WS.sub(" ", name.replace("\ufeff", "")).strip()


def read_header(path):
    """Return the CSV's first row as a list of column names."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        for row in reader:
            return row
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate NPS CSV export headers.")
    parser.add_argument("--brand", required=True, help="BWM, EDRS or EDM")
    parser.add_argument("--file", required=True, help="Path to the CSV export")
    args = parser.parse_args(argv)

    brand = args.brand.strip().upper()
    if brand not in EXPECTED:
        print(f"check_headers: unknown brand '{args.brand}'", file=sys.stderr)
        return 2

    try:
        header = read_header(args.file)
    except OSError as exc:
        print(f"check_headers: cannot read '{args.file}': {exc}", file=sys.stderr)
        return 2

    if not header:
        print(f"check_headers: '{args.file}' has no header row", file=sys.stderr)
        return 2

    actual = {normalize(h) for h in header if normalize(h)}
    expected = {normalize(h) for h in EXPECTED[brand] if normalize(h)}

    missing = sorted(expected - actual)
    extra = sorted(actual - expected)

    print(
        f"check_headers: {brand} - {len(header)} columns found, "
        f"{len(expected)} expected, {len(missing)} missing, {len(extra)} unexpected"
    )

    for col in missing:
        print(f"  MISSING : {col}", file=sys.stderr)
    for col in extra:
        print(f"  NEW     : {col}", file=sys.stderr)

    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
