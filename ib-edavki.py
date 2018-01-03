#!/usr/bin/python

import urllib
import urllib2
import sys
import xml.etree.ElementTree
import datetime
import os
import glob
import copy
import argparse
from pprint import pprint
from xml.dom import minidom


bsRateXmlUrl = 'https://www.bsi.si/_data/tecajnice/dtecbs-l.xml';
normalAssets = ['STK']
derivateAssets = ['CFD', 'OPT']
ignoreAssets = ['CASH']



parser = argparse.ArgumentParser()
parser.add_argument("ibXmlFile", metavar="ib-xml-file", help="InteractiveBrokers XML ouput (see README.md on how to generate one)")
parser.add_argument("-y", metavar="report-year", type=int, default=datetime.date.today().year, help="Report will be generated for the provided calendar year (defaults to " + str(datetime.date.today().year) + ")")
parser.add_argument("-t", help="Change trade dates to previous year (see README.md)", action="store_true")
args = parser.parse_args()
ibXmlFilename = args.ibXmlFile
reportYear = args.y
test = args.t

if test == True:
    testYearDiff = reportYear - datetime.date.today().year - 1

''' Creating daily exchange rates object '''
bsRateXmlFilename = 'bsrate-' + str(datetime.date.today().year) + str(datetime.date.today().month) + str(datetime.date.today().day) + '.xml'
if not os.path.isfile(bsRateXmlFilename):
    for file in glob.glob("bsrate-*.xml"):
        os.remove(file)
    bsRateXmlFile = urllib.FancyURLopener()
    bsRateXmlFile.retrieve(bsRateXmlUrl, bsRateXmlFilename)
bsRateXml = xml.etree.ElementTree.parse(bsRateXmlFilename).getroot()
bsRates = bsRateXml.find('DtecBS')

rates = {}
for d in bsRateXml:
    date = d.attrib['datum'].translate(None, '-')
    rates[date] = {}
    for r in d:
        currency = r.attrib['oznaka']
        rates[date][currency] = r.text



''' Parsing IB XML '''
ibXml = xml.etree.ElementTree.parse(ibXmlFilename).getroot()
ibTrades = ibXml[0][0].find('Trades')
'''ibPositions = ibXml[0][0].find('OpenPositions')'''
ibFlexStatement = ibXml[0][0]

if test == True:
    statementStartDate = str(reportYear + testYearDiff) + '0101'
    statementEndDate = str(reportYear + testYearDiff) + '1231'
else:
    statementStartDate = str(reportYear) + '0101'
    statementEndDate = str(reportYear) + '1231'



''' Dictionary of stock trade arrays, each key represents one symbol '''
trades = {}

''' Get trades from IB XML and sort them by the symbol '''
for ibTrade in ibTrades:
    if ibTrade.attrib['assetCategory'] in ignoreAssets:
        continue

    if ibTrade.tag == 'Trade':
        trade = {
            'currency': ibTrade.attrib['currency'],
            'assetCategory': ibTrade.attrib['assetCategory'],
            'tradePrice': float(ibTrade.attrib['tradePrice']),
            'quantity': float(ibTrade.attrib['quantity']),
            'buySell': ibTrade.attrib['buySell'],
            'tradeDate': ibTrade.attrib['tradeDate'],
            'tradeTime': ibTrade.attrib['tradeTime'],
            'transactionID': ibTrade.attrib['transactionID'],
            'ibOrderID': ibTrade.attrib['ibOrderID'],
            'openCloseIndicator': ibTrade.attrib['openCloseIndicator']
        }
        if ibTrade.attrib['description'] != '':
            trade['description'] = ibTrade.attrib['description'];
        if ibTrade.attrib['isin'] != '':
            trade['isin'] = ibTrade.attrib['isin'];
        ''' Options have multipliers, i.e. a quantity of 1 with tradePrice 3 and multiplier 100 is actually an option for 100 stocks, worth 100 x 3 = 300 '''
        if 'multiplier' in ibTrade.attrib:
            trade['tradePrice'] = float(ibTrade.attrib['tradePrice']) * float(ibTrade.attrib['multiplier'])
        symbol = ibTrade.attrib['symbol']
        if symbol not in trades:
            trades[symbol] = []
        lastTrade = trade;
        trades[symbol].append(trade)

    elif ibTrade.tag == 'Lot' and lastTrade != None:
        if 'openTransactionIds' not in lastTrade:
            lastTrade['openTransactionIds'] = {}
        tid = ibTrade.attrib['transactionID']
        lastTrade['openTransactionIds'][tid] = float(ibTrade.attrib['quantity']);



''' Detect if trades are Normal or Derivates and if they are Opening or Closing positions
    Convert the price to EUR '''
for symbol in trades:
    beforeTradePosition = 0
    for trade in trades[symbol]:
        if trade['currency'] == 'EUR':
            trade['tradePriceEUR'] = trade['tradePrice']
        else:
            date = trade['tradeDate']
            currency = trade['currency']
            if date in rates and currency in rates[date]:
                rate = float(rates[date][currency])
            else:
                for i in range(0, 6):
                    date = str(int(date) - 1)
                    if date in rates and currency in rates[date]:
                        rate = float(rates[date][currency])
                        print('There is no exchange rate for ' + str(trade['tradeDate']) + ', using ' + str(date))
                        break;
                    if i == 6:
                        sys.exit('Error: There is no exchange rate for ' + str(date))
            trade['tradePriceEUR'] = trade['tradePrice'] / rate

        if (
                (trade['openCloseIndicator'] == 'O' and trade['quantity'] > 0) or
                (trade['openCloseIndicator'] == 'C' and trade['quantity'] < 0)
            ):
            ''' Long position '''
            trade['positionType'] = 'long'
            if trade['assetCategory'] in normalAssets:
                trade['assetType'] = 'normal'
            elif trade['assetCategory'] in derivateAssets:
                trade['assetType'] = 'derivate'
        else:
            ''' Short position '''
            trade['positionType'] = 'short'
            trade['assetType'] = 'derivate'



''' Filter trades to only include those that closed in the parameter year and trades that opened the closing position '''
yearTrades = {}
for symbol in trades:
    for trade in trades[symbol]:
        if trade['tradeDate'][0:4] == str(reportYear) and trade['openCloseIndicator'] == 'C':
            if symbol not in yearTrades:
                yearTrades[symbol] = []
            for xtrade in trades[symbol]:
                if xtrade['transactionID'] in trade['openTransactionIds']:
                    ctrade = copy.copy(xtrade)
                    tid = ctrade['transactionID']
                    ctrade['quantity'] = trade['openTransactionIds'][tid]
                    yearTrades[symbol].append(ctrade)

            yearTrades[symbol].append(trade)



''' Logical trade order can be executed as multiple suborders at different price. Merge suborders in a single logical order. '''
mergedTrades = {}
for symbol in yearTrades:
    for trade in yearTrades[symbol]:
        tradeExists = False
        if symbol in mergedTrades:
            for previousTrade in mergedTrades[symbol]:
                if previousTrade['ibOrderID'] == trade['ibOrderID']:
                    previousTrade['tradePrice'] = (previousTrade['quantity'] * previousTrade['tradePrice'] + trade['quantity'] * trade['tradePrice']) / (previousTrade['quantity'] + trade['quantity'])
                    previousTrade['quantity'] = previousTrade['quantity'] + trade['quantity']
                    tradeExists = True
                    break
        if tradeExists == False:
            if symbol not in mergedTrades:
                mergedTrades[symbol] = []
            mergedTrades[symbol].append(trade)



''' Sort the trades by time '''
for symbol in mergedTrades:
    l = sorted(mergedTrades[symbol], key=lambda k: "%s%s" % (k['tradeDate'], k['tradeTime']))
    mergedTrades[symbol] = l



''' Sort the trades in 3 categories '''
normalTrades = {}
derivateTrades = {}
shortTrades = {}

for symbol in mergedTrades:
    for trade in mergedTrades[symbol]:
        if trade['positionType'] == 'short':
            if symbol not in shortTrades:
                shortTrades[symbol] = []
            shortTrades[symbol].append(trade)
        elif trade['assetType'] == 'normal':
            if symbol not in normalTrades:
                normalTrades[symbol] = []
            normalTrades[symbol].append(trade)
        elif trade['assetType'] == 'derivate':
            if symbol not in derivateTrades:
                derivateTrades[symbol] = []
            derivateTrades[symbol].append(trade)
        else:
            sys.exit('Error: cannot figure out if trade is Normal or Derivate, Long or Short')

#pprint(normalTrades);
#pprint(derivateTrades);
#pprint(shortTrades);



''' Generate the files for Normal '''
envelope = xml.etree.ElementTree.Element("Envelope", xmlns="http://edavki.durs.si/Documents/Schemas/Doh_KDVP_9.xsd")
envelope.set('xmlns:edp', "http://edavki.durs.si/Documents/Schemas/EDP-Common-1.xsd")
header = xml.etree.ElementTree.SubElement(envelope, "edp:Header")
taxpayer = xml.etree.ElementTree.SubElement(header, "edp:taxpayer")
taxNumber = xml.etree.ElementTree.SubElement(taxpayer, "edp:taxNumber").text = '12345678'
taxpayerType = xml.etree.ElementTree.SubElement(taxpayer, "edp:taxpayerType").text = 'FO'
name = xml.etree.ElementTree.SubElement(taxpayer, "edp:name").text = 'Janez Novak'
address1 = xml.etree.ElementTree.SubElement(taxpayer, "edp:address1").text = 'Slovenska 1'
city = xml.etree.ElementTree.SubElement(taxpayer, "edp:city").text = 'Ljubljana'
postNumber = xml.etree.ElementTree.SubElement(taxpayer, "edp:postNumber").text = '1000'
postName = xml.etree.ElementTree.SubElement(taxpayer, "edp:postName").text = 'Ljubljana'
AttachmentList = xml.etree.ElementTree.SubElement(envelope, "edp:AttachmentList")
Signatures = xml.etree.ElementTree.SubElement(envelope, "edp:Signatures")
body = xml.etree.ElementTree.SubElement(envelope, "body")
bodyContent = xml.etree.ElementTree.SubElement(body, "edp:bodyContent")
Doh_KDVP = xml.etree.ElementTree.SubElement(body, "Doh_KDVP")
KDVP = xml.etree.ElementTree.SubElement(Doh_KDVP, "KDVP")
DocumentWorkflowID = xml.etree.ElementTree.SubElement(KDVP, "DocumentWorkflowID").text = 'O'
Year = xml.etree.ElementTree.SubElement(KDVP, "Year").text = statementEndDate[0:4]
PeriodStart = xml.etree.ElementTree.SubElement(KDVP, "PeriodStart").text = statementStartDate[0:4] + '-' + statementStartDate[4:6] + '-' + statementStartDate[6:8]
PeriodEnd = xml.etree.ElementTree.SubElement(KDVP, "PeriodEnd").text = statementEndDate[0:4] + '-' + statementEndDate[4:6] + '-' + statementEndDate[6:8]
IsResident = xml.etree.ElementTree.SubElement(KDVP, "IsResident").text = 'true'
SecurityCount = xml.etree.ElementTree.SubElement(KDVP, "SecurityCount").text = str(len(normalTrades))
SecurityShortCount = xml.etree.ElementTree.SubElement(KDVP, "SecurityShortCount").text = '0'
SecurityWithContractCount = xml.etree.ElementTree.SubElement(KDVP, "SecurityWithContractCount").text = '0'
SecurityWithContractShortCount = xml.etree.ElementTree.SubElement(KDVP, "SecurityWithContractShortCount").text = '0'
ShareCount = xml.etree.ElementTree.SubElement(KDVP, "ShareCount").text = '0'

for symbol in normalTrades:
    KDVPItem = xml.etree.ElementTree.SubElement(Doh_KDVP, "KDVPItem")
    InventoryListType = xml.etree.ElementTree.SubElement(KDVPItem, "InventoryListType").text = 'PLVP'
    Name = xml.etree.ElementTree.SubElement(KDVPItem, "Name").text = symbol
    HasForeignTax = xml.etree.ElementTree.SubElement(KDVPItem, "HasForeignTax").text = 'false'
    HasLossTransfer = xml.etree.ElementTree.SubElement(KDVPItem, "HasLossTransfer").text = 'false'
    ForeignTransfer = xml.etree.ElementTree.SubElement(KDVPItem, "ForeignTransfer").text = 'false'
    TaxDecreaseConformance = xml.etree.ElementTree.SubElement(KDVPItem, "TaxDecreaseConformance").text = 'false'
    Securities = xml.etree.ElementTree.SubElement(KDVPItem, "Securities")
    if len(normalTrades[symbol]) > 0 and 'isin' in normalTrades[symbol][0]:
        ISIN = xml.etree.ElementTree.SubElement(Securities, "ISIN").text = normalTrades[symbol][0]['isin']
    Code = xml.etree.ElementTree.SubElement(Securities, "Code").text = symbol
    if len(normalTrades[symbol]) > 0 and 'description' in normalTrades[symbol][0]:
        Name = xml.etree.ElementTree.SubElement(Securities, "Name").text = normalTrades[symbol][0]['description']
    IsFond = xml.etree.ElementTree.SubElement(Securities, "IsFond").text = 'false'

    F8Value = 0
    n = -1
    for trade in normalTrades[symbol]:
        n += 1
        if test == True:
            tradeYear = int(trade['tradeDate'][0:4]) + testYearDiff
        else:
            tradeYear = int(trade['tradeDate'][0:4])
        Row = xml.etree.ElementTree.SubElement(Securities, "Row")
        ID = xml.etree.ElementTree.SubElement(Row, "ID").text = str(n)
        if trade['quantity'] > 0:
            PurchaseSale = xml.etree.ElementTree.SubElement(Row, "Purchase")
            F1 = xml.etree.ElementTree.SubElement(PurchaseSale, "F1").text = str(tradeYear) + '-' + trade['tradeDate'][4:6]+ '-' + trade['tradeDate'][6:8]
            F2 = xml.etree.ElementTree.SubElement(PurchaseSale, "F2").text = 'B'
            F3 = xml.etree.ElementTree.SubElement(PurchaseSale, "F3").text = '{0:.4f}'.format(trade['quantity'])
            F4 = xml.etree.ElementTree.SubElement(PurchaseSale, "F4").text = '{0:.4f}'.format(trade['tradePriceEUR'])
            F5 = xml.etree.ElementTree.SubElement(PurchaseSale, "F5").text = '0.0000'
        else:
            PurchaseSale = xml.etree.ElementTree.SubElement(Row, "Sale")
            F6 = xml.etree.ElementTree.SubElement(PurchaseSale, "F6").text = str(tradeYear) + '-' + trade['tradeDate'][4:6]+ '-' + trade['tradeDate'][6:8]
            F7 = xml.etree.ElementTree.SubElement(PurchaseSale, "F7").text = '{0:.4f}'.format(-trade['quantity'])
            F9 = xml.etree.ElementTree.SubElement(PurchaseSale, "F9").text = '{0:.4f}'.format(trade['tradePriceEUR'])
        F8Value += trade['quantity']
        F8 = xml.etree.ElementTree.SubElement(Row, "F8").text = '{0:.4f}'.format(F8Value)


xmlString = xml.etree.ElementTree.tostring(envelope)
prettyXmlString = minidom.parseString(xmlString).toprettyxml(indent="\t")
with open("Doh-KDVP.xml", "w") as f:
    f.write(prettyXmlString.encode('utf-8'))



''' Generate the files for Derivates and Shorts '''
envelope = xml.etree.ElementTree.Element("Envelope", xmlns="http://edavki.durs.si/Documents/Schemas/D_IFI_3.xsd")
envelope.set('xmlns:edp', "http://edavki.durs.si/Documents/Schemas/EDP-Common-1.xsd")
header = xml.etree.ElementTree.SubElement(envelope, "edp:Header")
taxpayer = xml.etree.ElementTree.SubElement(header, "edp:taxpayer")
taxNumber = xml.etree.ElementTree.SubElement(taxpayer, "edp:taxNumber").text = '12345678'
taxpayerType = xml.etree.ElementTree.SubElement(taxpayer, "edp:taxpayerType").text = 'FO'
name = xml.etree.ElementTree.SubElement(taxpayer, "edp:name").text = 'Janez Novak'
address1 = xml.etree.ElementTree.SubElement(taxpayer, "edp:address1").text = 'Slovenska 1'
city = xml.etree.ElementTree.SubElement(taxpayer, "edp:city").text = 'Ljubljana'
postNumber = xml.etree.ElementTree.SubElement(taxpayer, "edp:postNumber").text = '1000'
postName = xml.etree.ElementTree.SubElement(taxpayer, "edp:postName").text = 'Ljubljana'
AttachmentList = xml.etree.ElementTree.SubElement(envelope, "edp:AttachmentList")
Signatures = xml.etree.ElementTree.SubElement(envelope, "edp:Signatures")
body = xml.etree.ElementTree.SubElement(envelope, "body")
bodyContent = xml.etree.ElementTree.SubElement(body, "edp:bodyContent")
difi = xml.etree.ElementTree.SubElement(body, "D_IFI")
DocumentWorkflowID = xml.etree.ElementTree.SubElement(difi, "DocumentWorkflowID").text = 'O'
PeriodStart = xml.etree.ElementTree.SubElement(difi, "PeriodStart").text = statementStartDate[0:4] + '-' + statementStartDate[4:6] + '-' + statementStartDate[6:8]
PeriodEnd = xml.etree.ElementTree.SubElement(difi, "PeriodEnd").text = statementEndDate[0:4] + '-' + statementEndDate[4:6] + '-' + statementEndDate[6:8]
TelephoneNumber = xml.etree.ElementTree.SubElement(difi, "TelephoneNumber").text = '012345678'
Email = xml.etree.ElementTree.SubElement(difi, "Email").text = 'noreply@furs.si'


n = 0
for symbol in derivateTrades:
    n += 1
    TItem = xml.etree.ElementTree.SubElement(difi, "TItem")
    Id = xml.etree.ElementTree.SubElement(TItem, "Id").text = str(n)
    TypeId = xml.etree.ElementTree.SubElement(TItem, "TypeId").text = 'PLIFI'
    if derivateTrades[symbol][0]['assetCategory'] == 'CFD':
        Type = xml.etree.ElementTree.SubElement(TItem, "Type").text = '02'
        TypeName = xml.etree.ElementTree.SubElement(TItem, "TypeName").text = 'financne pogodbe na razliko'
    elif derivateTrades[symbol][0]['assetCategory'] == 'OPT':
        Type = xml.etree.ElementTree.SubElement(TItem, "Type").text = '03'
        TypeName = xml.etree.ElementTree.SubElement(TItem, "TypeName").text = 'opcija in certifikat'
    else:
        Type = xml.etree.ElementTree.SubElement(TItem, "Type").text = '04'
        TypeName = xml.etree.ElementTree.SubElement(TItem, "TypeName").text = 'drugo'
    if len(derivateTrades[symbol]) > 0 and 'description' in derivateTrades[symbol][0]:
        Name = xml.etree.ElementTree.SubElement(TItem, "Name").text = derivateTrades[symbol][0]['description']
    if derivateTrades[symbol][0]['assetCategory'] != 'OPT':
        ''' Option symbols are to long and not accepted by eDavki '''
        Code = xml.etree.ElementTree.SubElement(TItem, "Code").text = symbol
    if len(derivateTrades[symbol]) > 0 and 'isin' in derivateTrades[symbol][0]:
        ISIN = xml.etree.ElementTree.SubElement(TItem, "ISIN").text = derivateTrades[symbol][0]['isin']
    HasForeignTax = xml.etree.ElementTree.SubElement(TItem, "HasForeignTax").text = 'false'

    F8Value = 0
    for trade in derivateTrades[symbol]:
        if test == True:
            tradeYear = int(trade['tradeDate'][0:4]) + testYearDiff
        else:
            tradeYear = int(trade['tradeDate'][0:4])
        TSubItem = xml.etree.ElementTree.SubElement(TItem, "TSubItem")
        ItemId = xml.etree.ElementTree.SubElement(TSubItem, "ItemId").text = str(n)
        if trade['quantity'] > 0:
            PurchaseSale = xml.etree.ElementTree.SubElement(TSubItem, "Purchase")
            F1 = xml.etree.ElementTree.SubElement(PurchaseSale, "F1").text = str(tradeYear) + '-' + trade['tradeDate'][4:6]+ '-' + trade['tradeDate'][6:8]
            F2 = xml.etree.ElementTree.SubElement(PurchaseSale, "F2").text = 'A'
            F3 = xml.etree.ElementTree.SubElement(PurchaseSale, "F3").text = '{0:.4f}'.format(trade['quantity'])
            F4 = xml.etree.ElementTree.SubElement(PurchaseSale, "F4").text = '{0:.4f}'.format(trade['tradePriceEUR'])
        else:
            PurchaseSale = xml.etree.ElementTree.SubElement(TSubItem, "Sale")
            F5 = xml.etree.ElementTree.SubElement(PurchaseSale, "F5").text = str(tradeYear) + '-' + trade['tradeDate'][4:6]+ '-' + trade['tradeDate'][6:8]
            F6 = xml.etree.ElementTree.SubElement(PurchaseSale, "F6").text = '{0:.4f}'.format(-trade['quantity'])
            F7 = xml.etree.ElementTree.SubElement(PurchaseSale, "F7").text = '{0:.4f}'.format(trade['tradePriceEUR'])
        F8Value += trade['quantity']
        F8 = xml.etree.ElementTree.SubElement(TSubItem, "F8").text = '{0:.4f}'.format(F8Value)


for symbol in shortTrades:
    n += 1
    TItem = xml.etree.ElementTree.SubElement(difi, "TItem")
    Id = xml.etree.ElementTree.SubElement(TItem, "Id").text = str(n)
    TypeId = xml.etree.ElementTree.SubElement(TItem, "TypeId").text = 'PLIFIShort'
    Type = xml.etree.ElementTree.SubElement(TItem, "Type").text = '04'
    TypeName = xml.etree.ElementTree.SubElement(TItem, "TypeName").text = 'drugo'
    if len(shortTrades[symbol]) > 0 and 'description' in shortTrades[symbol][0]:
        Name = xml.etree.ElementTree.SubElement(TItem, "Name").text = shortTrades[symbol][0]['description']
    Code = xml.etree.ElementTree.SubElement(TItem, "Code").text = symbol
    if len(shortTrades[symbol]) > 0 and 'isin' in shortTrades[symbol][0]:
        ISIN = xml.etree.ElementTree.SubElement(TItem, "ISIN").text = shortTrades[symbol][0]['isin']
    HasForeignTax = xml.etree.ElementTree.SubElement(TItem, "HasForeignTax").text = 'false'

    F8Value = 0
    for trade in shortTrades[symbol]:
        if test == True:
            tradeYear = int(trade['tradeDate'][0:4]) + testYearDiff
        else:
            tradeYear = int(trade['tradeDate'][0:4])
        TShortSubItem = xml.etree.ElementTree.SubElement(TItem, "TShortSubItem")
        ItemId = xml.etree.ElementTree.SubElement(TShortSubItem, "ItemId").text = str(n)
        if trade['quantity'] > 0:
            PurchaseSale = xml.etree.ElementTree.SubElement(TShortSubItem, "Purchase")
            F4 = xml.etree.ElementTree.SubElement(PurchaseSale, "F4").text = str(tradeYear) + '-' + trade['tradeDate'][4:6]+ '-' + trade['tradeDate'][6:8]
            F5 = xml.etree.ElementTree.SubElement(PurchaseSale, "F5").text = 'A'
            F6 = xml.etree.ElementTree.SubElement(PurchaseSale, "F6").text = '{0:.4f}'.format(trade['quantity'])
            F7 = xml.etree.ElementTree.SubElement(PurchaseSale, "F7").text = '{0:.4f}'.format(trade['tradePriceEUR'])
        else:
            PurchaseSale = xml.etree.ElementTree.SubElement(TShortSubItem, "Sale")
            F1 = xml.etree.ElementTree.SubElement(PurchaseSale, "F1").text = str(tradeYear) + '-' + trade['tradeDate'][4:6]+ '-' + trade['tradeDate'][6:8]
            F2 = xml.etree.ElementTree.SubElement(PurchaseSale, "F2").text = '{0:.4f}'.format(-trade['quantity'])
            F3 = xml.etree.ElementTree.SubElement(PurchaseSale, "F3").text = '{0:.4f}'.format(trade['tradePriceEUR'])
        F8Value += trade['quantity']
        F8 = xml.etree.ElementTree.SubElement(TShortSubItem, "F8").text = '{0:.4f}'.format(F8Value)

xmlString = xml.etree.ElementTree.tostring(envelope)
prettyXmlString = minidom.parseString(xmlString).toprettyxml(indent="\t")
with open("D-IFI.xml", "w") as f:
    f.write(prettyXmlString.encode('utf-8'))
