# Marine InVEST: Coastal Protection (Profile Generator)
# Authors: Greg Guannel, Gregg Verutes
# 09/08/10

import numpy as num
import CPf_SignalSmooth as SignalSmooth
import string, sys, os, time, datetime, shlex
import fpformat, operator
import arcgisscripting
import shutil

from win32com.client import Dispatch
from scipy.interpolate import interp1d
from scipy import optimize
from math import *
from matplotlib import *
from pylab import *

# create the geoprocessor object
gp = arcgisscripting.create()

# set output handling
gp.OverwriteOutput = 1
# check out any necessary extensions
gp.CheckOutExtension("management")
gp.CheckOutExtension("analysis")
gp.CheckOutExtension("conversion")
gp.CheckOutExtension("spatial")

# error messages
msgArguments = "Problem with arguments."

try:
    # get parameters
    parameters = []
    now = datetime.datetime.now()
    parameters.append("Date and Time: "+ now.strftime("%Y-%m-%d %H:%M"))
    gp.workspace = gp.GetParameterAsText(0)
    parameters.append("Workspace: "+ gp.workspace)
    LandPoint = gp.GetParameterAsText(1)
    parameters.append("Land Point: "+ LandPoint)
    LandPoly = gp.GetParameterAsText(2)
    parameters.append("Land Polygon: "+ LandPoly)
    InputTable = gp.GetParameterAsText(3)
    parameters.append("Profile Generator Excel Table: "+ InputTable)
    ProfileQuestion = gp.GetParameterAsText(4)
    parameters.append("Do you have a nearshore bathymetry GIS layer?: "+ ProfileQuestion)
    BathyGrid = gp.GetParameterAsText(5)
    parameters.append("IF 1: Bathymetric Grid (DEM): "+ BathyGrid)
    CSProfile = gp.GetParameterAsText(6)
    parameters.append("IF 2: Upload Your Cross-Shore Profile: "+ CSProfile)
    SmoothParameter = gp.GetParameterAsText(7)
    parameters.append("Smoothing Parameter: "+ SmoothParameter)
    WW3_Pts = gp.GetParameterAsText(8)
    parameters.append("Wave Watch 3 Model Data: "+ WW3_Pts)
    FetchQuestion = gp.GetParameterAsText(9)
    parameters.append("Do you wish to calculate fetch for LandPoint?: "+ FetchQuestion)
except:
    raise Exception, msgArguments + gp.GetMessages(2)

try:
    thefolders=["intermediate","Output","scratch"]
    for folder in thefolders:
        if not gp.exists(gp.workspace+folder):
            gp.CreateFolder_management(gp.workspace, folder)
except:
    raise Exception, "Error creating folders"

# variables (hard-coded)
BufferDist = 150
SampInterval = 1
TransectDist = 1.0
BearingsNum = 16
RadLineDist = 100000

# intermediate and output directories
outputws = gp.workspace + os.sep + "Output" + os.sep
interws = gp.workspace + os.sep + "intermediate" + os.sep
scratchws = gp.workspace + os.sep + "scratch" + os.sep

PT1 = interws + "PT1.shp"
PT2 = interws + "PT2.shp"
PT1_Z = interws + "PT1_Z.shp"
PT2_Z = interws + "PT2_Z.shp"
LandPoint_Buff = interws + "LandPoint_Buff.shp"
LandPoint_Buff100k = interws + "LandPoint_Buff100k.shp"
LandPoint_Geo = interws + "LandPoint_Geo.shp"
Shoreline = interws + "Shoreline.shp"
Shoreline_Buff_Clip = interws + "Shoreline_Buff_Clip.shp"
Shoreline_Buff_Clip_Diss = interws + "Shoreline_Buff_Clip_Diss.shp"
PtsCopy = interws + "PtsCopy.shp"
PtsCopy2 = interws + "PtsCopy2.shp"
PtsCopyLR = interws + "PtsCopy2_lineRotate.shp"
Fetch_AOI = interws + "Fetch_AOI.shp"
UnionFC = interws + "UnionFC.shp"
SeaPoly = interws + "SeaPoly.shp"
seapoly_rst = interws + "seapoly_rst"
seapoly_e = interws + "seapoly_e"
PtsCopyEL = interws + "PtsCopy2_eraseLand.shp"
PtsCopyExp = interws + "PtsCopy2_explode.shp"
PtsCopyExp_Lyr = interws + "PtsCopy2_explode.lyr"
WW3_Pts_prj = interws + "WW3_Pts_prj.shp"
costa_ww3 = interws + "costa_ww3"
LandPoint_WW3 = interws + "LandPoint_WW3.shp"

BathyProfile = outputws + "BathyProfile.txt"
CreatedProfile = outputws + "CreatedProfile.txt"
Profile_Pts = outputws + "Profile_Pts.shp"
Profile_Plot = outputws + "Profile_Plot.png"
Fetch_Plot = outputws + "Fetch_Plot.png"
Fetch_Vectors = outputws + "Fetch_Vectors.shp"
ProfileErosion_HTML = outputws + "ProfileErosion_Results.html"

# various functions and checks
def AddField(FileName, FieldName, Type, Precision, Scale):
    fields = gp.ListFields(FileName, FieldName)
    field_found = fields.Next()
    if field_found:
        gp.DeleteField_management(FileName, FieldName)
    gp.AddField_management(FileName, FieldName, Type, Precision, Scale, "", "", "NON_NULLABLE", "NON_REQUIRED", "")
    return FileName

def getDatum(thedata):
    desc = gp.describe(thedata)
    SR = desc.SpatialReference
    if SR.Type == "Geographic":
        strDatum = SR.DatumName         
    else:
        gp.OutputCoordinateSystem = SR
        strSR = str(gp.OutputCoordinateSystem)
        gp.OutputCoordinateSystem = ""
        n1 = strSR.find("GEOGCS")
        n2 = strSR.find("PROJECTION")
        strDatum = strSR[n1:n2-1]
    return strDatum

def ckDatum(thedata):
    desc = gp.describe(thedata)
    SR = desc.SpatialReference
    if SR.Type == "Geographic":
        strDatum = SR.DatumName         
    else:
        gp.OutputCoordinateSystem = SR
        strSR = str(gp.OutputCoordinateSystem)
        gp.OutputCoordinateSystem = ""
        n1 = strSR.find("DATUM[\'")
        n2 = strSR.find("\'",n1+7)
        strDatum = strSR[n1+7:n2]
    if strDatum == "D_WGS_1984":
        pass
    else:
        gp.AddError(thedata+" is not a valid input.\nThe model requires data inputs and a projection with the \"WGS84\" datum.\nSee InVEST FAQ document for how to reproject datasets.")
        raise Exception

def ckProjection(data):
    dataDesc = gp.describe(data)
    spatreflc = dataDesc.SpatialReference
    if spatreflc.Type <> 'Projected':
        gp.AddError(data +" does not appear to be projected.  It is assumed to be in meters.")
        raise Exception
    if spatreflc.LinearUnitName <> 'Meter':
        gp.AddError("This model assumes that "+data+" is projected in meters for area calculations.  You may get erroneous results.")
        raise Exception
    
def grabProjection(data):
    dataDesc = gp.describe(data)
    sr = dataDesc.SpatialReference
    gp.OutputCoordinateSystem = sr
    strSR = str(gp.OutputCoordinateSystem)
    return strSR

def compareProjections(LandPoint, LandPoly):
    if gp.describe(LandPoint).SpatialReference.name <> gp.describe(LandPoly).SpatialReference.name:
        gp.AddError("Projection Error: "+LandPoint+" is in a different projection from the LandPoly data.  The two inputs must be the same projection to calculate depth profile.")
        raise Exception

def PTCreate(PTType, midx, midy, TransectDist): # function to create point transects
    if PTType == 1:
        y1 = midy + TransectDist
        y2 = midy - TransectDist
        x1 = midx
        x2 = midx
    elif PTType == 2:
        y1 = midy
        y2 = midy 
        x1 = midx + TransectDist
        x2 = midx - TransectDist
    elif PTType == 3:
        y1 = NegRecip*(TransectDist) + midy
        y2 = NegRecip*(-TransectDist) + midy
        x1 = midx + TransectDist
        x2 = midx - TransectDist
    elif PTType == 4:
        y1 = midy + TransectDist
        y2 = midy - TransectDist
        x1 = (TransectDist/NegRecip) + midx
        x2 = (-TransectDist/NegRecip) + midx
    elif PTType == 5:
        y1 = midy + TransectDist
        y2 = midy - TransectDist
        x1 = (TransectDist/NegRecip) + midx
        x2 = (-TransectDist/NegRecip) + midx
    elif PTType == 6:
        y1 = NegRecip*(TransectDist) + midy
        y2 = NegRecip*(-TransectDist) + midy
        x1 = midx + TransectDist
        x2 = midx - TransectDist
    return x1, y1, x2, y2

def Indexed(x,value): #Locates index of point in vector x that has closest value as variable value
    mylist=abs(x-value);    
    if isinstance(x,num.ndarray):
        mylist=mylist.tolist()
    minval=min(mylist)
    ind=[i for i, v in enumerate(mylist) if v == minval]
    ind=ind[0]
    return ind

def SlopeModif(X,Y,SlopeMod,OffMod,ShoreMod):  #Replaces/adds linear portion to profile
    m=1.0/SlopeMod; #Slope
    Xend=X[-1]; #Last point in profile
    if ShoreMod<Xend: #if modified portion in within profile
        of=Indexed(X,OffMod)#Locate offshore point
        sho=Indexed(X,ShoreMod)#Locate shoreward point
            
        #Modify the slope between offshore and shoreward points
        Y[of:sho]=m*X[of:sho]+Y[of]-m*X[of]
    else:
        of=Indexed(X,OffMod)#Locate offshore point
        dist=ShoreMod-OffMod;
        temp_x=num.arange(0,int(dist),1)#Length of the segment modified/added
        out=num.arange(Indexed(temp_x,dist)+1,len(temp_x),1); #Remove points that are beyond shoreward limit 
        temp_y=m*temp_x+Y[of];temp_y=num.delete(temp_y,out,None); #New profile
        Y=num.append(Y[0:of-1],temp_y,None); #Append depth vector
        X=num.append(X[0:of-1],temp_x+X[of],None) #append X vector

        #Resample on vector with dx=1;
        F=interp1d(X,Y);X=num.arange(0,len(X),1);
        Y=F(X);
    return X,Y

def DataRemove(X,Y,OffDel,ShoreDel):  #Remove date from transect 
    of=Indexed(Xmod,OffDel);sho=Indexed(Xmod,ShoreDel)#Locate offshore and shoreward points
    out=num.arange(of,sho+1,1);
    Y=num.delete(Y,out,None); #Remove points from Ymod
    X=num.delete(X,out,None); #Remove points from Xmod
    X=num.arange(0,len(X),1) #Resample X-axis
    return X,Y

#___check that correct inputs were provided based on 'ProfileQuestion'
if ProfileQuestion == "(1) Yes":
    if not BathyGrid:
        gp.AddError("A bathymetry grid input is required.")
        raise Exception
elif ProfileQuestion == "(2) No, but I will upload a cross-shore profile":
    if not CSProfile:
        gp.AddError("A cross-shore profile input is required.")
        raise Exception

#_____check that datum is WGS84 and projected in meters
ckDatum(LandPoint) 
# check that three inputs are projected
ckProjection(LandPoint)
ckProjection(LandPoly)
if BathyGrid:
    ckProjection(BathyGrid)
geo_projection = getDatum(LandPoint) # get datum of 'LandPoint'

#_____import Profile Builder info from Excel file
xlApp = Dispatch("Excel.Application")
xlApp.Visible=0
xlApp.DisplayAlerts=0
xlApp.Workbooks.Open(InputTable)
cell = xlApp.Worksheets("ProfileGeneratorInput")
cell1 = xlApp.Worksheets("HelpCreatingBackshoreProfile")
cell2 = xlApp.Worksheets("UploadedProfileModification")

# Wave climate data
WaveClimateCheck = cell.Range("e47").Value # 1 model provides He,Hmod,Tmod; 2 user enters data
if WaveClimateCheck == 2:    # load wave climate info
    He=cell.Range("h18").Value # effective wave height
    Hm=cell.Range("i18").Value # modal wave height
    Tm=cell.Range("j18").Value # modal wave period

# Tide information        
MSL = cell.Range("d23").Value # mean sea level
HT = cell.Range("e23").Value # high tide elevation
HAT = cell.Range("f23").Value # highest tide elevation
hc=-ceil(1.57*He) # closure depth 

#__Check if user needs backshore help
BackHelp=cell.Range("e48").Value #1: need prof. builder, 2: modifies, 3: No change

# Beach parameters
Diam = cell.Range("e8").Value # Sediment diam [mm]
A = cell.Range("e49").Value # sediment scale factor

if BackHelp==1: #Read Profile Build Information
    # Foreshore    
    Slope = cell1.Range("f7").Value # foreshore slope = 1/Slope
    m =1.0/Slope; # bed slope

    # Read HelpCreatingBackshoreProfile Sheet
    DuneCheck = cell1.Range("f33").Value # 1 don't know, 2 no, 3 don't know, 4 Yes
    BermCrest = cell1.Range("f16").Value
    BermLength = cell1.Range("g16").Value

    #Estimate dune size from Short and Hesp
    if DuneCheck==1 or DuneCheck==3: 
        Hb=0.39*9.81**(1.0/5)*(Tm*Hm**2)**(2.0/5)
        a=0.00000126
        b=num.sqrt(3.61**2+1.18*(1.56*9.81*(Diam/1000.0)**3/a**2)**(1.0/1.53))-3.61
        ws=(a*b**1.53)/(Diam/1000) # Fall velocity
        RTR=HT/Hb
            
        if RTR>3: # in this case, beach is not wave dominated, can't know value, so take zero
            DuneCrest=0
            BermLength=50
        else: # else, beach is wave dominated, we read Short and Hesp
            Type=Hb/(ws*Tm)
            if Type<3:
                DuneCrest=5
            elif Type<4:
                DuneCrest=10
            elif Type<5:
                DuneCrest=12
            elif Type<6:
                DuneCrest=20
            else:
                DuneCrest=23
                    
    elif DuneCheck==2: # no dunes
        DuneCrest=0
        BermLength=50 # beach has no dune and infinitely long berm
            
    elif DuneCheck==4: # user has data
        DuneCrest = cell1.Range("j25").Value
elif BackHelp==2: # Read UploadedProfileModification sheet
    SlopeMod1=cell2.Range("e6").Value
    SlopeMod2=cell2.Range("e7").Value
    SlopeMod3=cell2.Range("e8").Value
    OffMod1=cell2.Range("f6").Value
    OffMod2=cell2.Range("f7").Value
    OffMod3=cell2.Range("f8").Value
    ShoreMod1=cell2.Range("g6").Value
    ShoreMod2=cell2.Range("g7").Value
    ShoreMod3=cell2.Range("g8").Value

    OffDel1=cell2.Range("e11").Value
    OffDel2=cell2.Range("e12").Value
    ShoreDel1=cell2.Range("f11").Value
    ShoreDel2=cell2.Range("f12").Value

xlApp.ActiveWorkbook.Close(SaveChanges=0)
xlApp.Quit()
    
#_____Cut, read or create nearshore bathy profile
if ProfileQuestion == "(1) Yes": # model extracts value from GIS layers
    gp.AddMessage("\nCreating Point Transects...")
    # create transect and read transect file
    gp.Buffer_analysis(LandPoint, LandPoint_Buff, str(BufferDist)+" Meters", "FULL", "ROUND", "NONE", "")
    gp.Extent = LandPoint_Buff
    gp.PolygonToLine_management(LandPoly, Shoreline)
    gp.Extent = ""
    gp.Clip_analysis(Shoreline, LandPoint_Buff, Shoreline_Buff_Clip, "")
    # check to make sure that clipped shoreline is not empty FC
    if gp.GetCount_management(Shoreline_Buff_Clip) == 0:
        gp.AddError("Shoreline was not found within "+str(BufferDist)+" meters of 'LandPoint' input.  \
                     Either increase the buffer distance or move the 'LandPoint' input closer to the coastline.")
        raise Exception
    gp.Dissolve_management(Shoreline_Buff_Clip, Shoreline_Buff_Clip_Diss, "", "", "MULTI_PART", "UNSPLIT_LINES")

    # set coordinate system to same projection (in meters) as the shoreline point input
    gp.outputCoordinateSystem = LandPoint
    cur = gp.UpdateCursor(LandPoint)
    row = cur.Next()
    feat = row.Shape
    midpoint = feat.Centroid
    midList = shlex.split(midpoint)
    midList = [float(s) for s in midList]
    midx = midList[0]
    midy = midList[1]
    del cur
    del row

    # grab coordinates of the start and end of the coastline segment
    cur = gp.SearchCursor(Shoreline_Buff_Clip_Diss)
    row = cur.Next()
    counter = 1
    feat = row.Shape
    firstpoint = feat.FirstPoint
    lastpoint = feat.LastPoint
    startList = shlex.split(firstpoint)
    endList = shlex.split(lastpoint)
    startx = float(startList[0])
    starty = float(startList[1])
    endx = float(endList[0])
    endy = float(endList[1])

    # diagnose the type of perpendicular transect to create (PerpTransType)
    PerpTransType = 0
    if starty==endy or startx==endx:
        if starty == endy:
            y1 = midy + TransectDist
            y2 = midy - TransectDist
            x1 = midx
            x2 = midx
            PerpTransType = 1
        if startx == endx:
            y1 = midy
            y2 = midy 
            x1 = midx + TransectDist
            x2 = midx - TransectDist
            PerpTransType = 2
    else:
        # get the slope of the line
        m = ((starty - endy)/(startx - endx))
        # get the negative reciprocal
        NegRecip = -1*((startx - endx)/(starty - endy))

        if m > 0:
            # increase x-values, find y
            if m >= 1:
                y1 = NegRecip*(TransectDist) + midy
                y2 = NegRecip*(-TransectDist) + midy
                x1 = midx + TransectDist
                x2 = midx - TransectDist
                PerpTransType = 3
            # increase y-values, find x
            if m < 1:
                y1 = midy + TransectDist
                y2 = midy - TransectDist
                x1 = (TransectDist/NegRecip) + midx
                x2 = (-TransectDist/NegRecip) + midx
                PerpTransType = 4
        if m < 0:
            # add to x, find y-values
            if m >= -1:
            # add to y, find x-values
                y1 = midy + TransectDist
                y2 = midy - TransectDist
                x1 = (TransectDist/NegRecip) + midx
                x2 = (-TransectDist/NegRecip) + midx
                PerpTransType = 5
            if m < -1:
                y1 = NegRecip*(TransectDist) + midy
                y2 = NegRecip*(-TransectDist) + midy
                x1 = midx + TransectDist
                x2 = midx - TransectDist
                PerpTransType = 6
    del cur
    del row

    # grab projection spatial reference from 'LandPoint'
    dataDesc = gp.describe(LandPoint)
    spatialRef = dataDesc.SpatialReference
    gp.CreateFeatureClass_management(interws, "PT1.shp", "POINT", "#", "#", "#", spatialRef)
    gp.CreateFeatureClass_management(interws, "PT2.shp", "POINT", "#", "#", "#", spatialRef)

    # create two point transects, each point is 1 meter away from the previous    
    cur1 = gp.InsertCursor(PT1)
    cur2 = gp.InsertCursor(PT2)
    while TransectDist <= 10000:
        # call 'PTCreate' function to use the correct perpendicular transect formula based on coastline slope (m)
        x1, y1, x2, y2 = PTCreate(PerpTransType, midx, midy, TransectDist)
        row1 = cur1.NewRow()
        pnt = gp.CreateObject("POINT")
        pnt.x = x1
        pnt.y = y1
        row1.shape = pnt
        cur1.InsertRow(row1)
        row2 = cur2.NewRow()
        pnt = gp.CreateObject("POINT")
        pnt.x = x2
        pnt.y = y2
        row2.shape = pnt
        cur2.InsertRow(row2)
        TransectDist = TransectDist + 1
    del cur1, row1
    del cur2, row2

    # extract depth values from 'BathyGrid' to point transects
    gp.ExtractValuesToPoints_sa(PT1, BathyGrid, PT1_Z, "INTERPOLATE")
    gp.ExtractValuesToPoints_sa(PT2, BathyGrid, PT2_Z, "INTERPOLATE")
    PT1_Z = AddField(PT1_Z, "PT_ID", "LONG", "", "")        
    gp.CalculateField_management(PT1_Z, "PT_ID", "[FID]+1", "VB")
    PT2_Z = AddField(PT2_Z, "PT_ID", "LONG", "", "")        
    gp.CalculateField_management(PT2_Z, "PT_ID", "[FID]+1", "VB")    

    # create depth lists of two point transects
    Dmeas1 = []
    cur = gp.UpdateCursor(PT1_Z)
    row = cur.Next()
    while row:
        Dmeas1.append(row.GetValue("RASTERVALU"))
        cur.UpdateRow(row)
        row = cur.next()
    del cur, row
    Dmeas2 = []
    cur = gp.UpdateCursor(PT2_Z)
    row = cur.Next()
    while row:  
        Dmeas2.append(row.GetValue("RASTERVALU"))
        cur.UpdateRow(row)
        row = cur.next()
    del cur, row

    # find which point transect hits water first
    DepthStart1 = 1
    for DepthValue1 in Dmeas1:
        if DepthValue1 < 0.0 and DepthValue1 <> -9999.0:
            break
        DepthStart1 = DepthStart1 +1
    DepthStart2 = 1
    for DepthValue2 in Dmeas2:
        if DepthValue2 < 0.0 and DepthValue2 <> -9999.0:
            break
        DepthStart2 = DepthStart2 +1

    # create final lists of cross-shore distance (Dx) and depth (Dmeas)
    Dx = []   
    Dmeas = []
    counter = 0
    if DepthStart1 < DepthStart2:
        for i in range(DepthStart1-1,len(Dmeas1)):
            if Dmeas1[i] < 0.0 and Dmeas1[i] <> -9999.0:
                Dx.append(counter)
                Dmeas.append(Dmeas1[i])
                counter = counter + 1
            else:
                break
    else:
        for j in range(DepthStart2-1,len(Dmeas2)):
            if Dmeas2[j] < 0.0 and Dmeas2[j] <> -9999.0:
                Dx.append(counter)
                Dmeas.append(Dmeas2[j])
                counter = counter + 1
            else:
                break

    if len(Dmeas1) == 0 and len(Dmeas2) == 0:
        gp.AddError("Neither transect overlaps the seas.  Please check the location of your 'LandPoint' and bathymetry inputs.")
        raise Exception

    # create txt profile for bathy portion
    file = open(BathyProfile, "w")
    for i in range(0,len(Dmeas)):
        file.writelines(str(Dx[i])+"\t"+str(Dmeas[i])+"\n")
    file.close()

    # create final point transect file
    if DepthStart1 < DepthStart2:
        gp.Select_analysis(PT1_Z, Profile_Pts, "\"PT_ID\" > "+str(DepthStart1-1)+" AND \"PT_ID\" < "+str(DepthStart1+counter))
    else:
        gp.Select_analysis(PT2_Z, Profile_Pts, "\"PT_ID\" > "+str(DepthStart2-1)+" AND \"PT_ID\" < "+str(DepthStart2+counter))

    # smooth profile and create x axis
    lx=len(Dmeas) # length of original data
    Dx=num.array(Dx);xd=Dx[:];
    Dmeas=num.array(Dmeas);Dmeas=Dmeas[::-1] # reverse order so deeper values starts at x=0
    yd=SignalSmooth.smooth(Dmeas,int(SmoothParameter),'flat')

# upload user's profile
elif ProfileQuestion == "(2) No, but I will upload a cross-shore profile":
    # read in user's cross-shore profile
    TextData = open(CSProfile,"r") 
    Dx=[];Dmeas=[];
    for line in TextData.readlines():
        linelist= [float(s) for s in line.split("\t")] # split the list by tab delimiter
        Dx.append(linelist[0])
        Dmeas.append(linelist[1])
    Dmeas=num.array(Dmeas);Dx=num.array(Dx);lx=len(Dx);
    xd=Dx[:];
    yd=SignalSmooth.smooth(Dmeas,int(SmoothParameter),'flat')

# equilibrium beach profile; in case we don't have nearshore bathy
elif ProfileQuestion == "(3) No, please assume an equilibrium beach profile":
    x=num.arange(0,10001,1) # long axis
    temp=2.0/3
    Dmeas=x**(temp)# eq. profile
    out=num.nonzero(Dmeas>-hc)
    Dmeas=num.delete(Dmeas,out,None)# water depths down to hc
    Dx=num.delete(x,out,None);lx=len(Dx);
    Dmeas=-Dmeas[::-1] # reverse order so deeper values starts at x=0
    yd=Dmeas[:];xd=Dx[:];

#___Profile modification
gp.AddMessage("\nCustomizing Depth Profile...")

if BackHelp==1: # Create Backshore profile  
    x=num.arange(0,10001,1) # long axis

    # add foreshore
    yf=1.0/Slope*x+yd[-1]
    Above=num.nonzero(yf>BermCrest)
    xf=num.delete(x,Above[0],None)
    yf=num.delete(yf,Above[0],None) # remove values that are above BermCrest

    # berm and dune
    if DuneCheck == 2: # no dunes are present, just berm   # 1 = DK, 2 = No, 3 = Maybe 4 = Have data
        xb=num.arange(0,100,1)
        yb=num.array(len(xb)*[0.0])+BermCrest # horizontal berm 100m long
    elif DuneCheck == 1 and RTR > 3: # user doesn't know, and not wave Dominated: no dunes, just berm
        xb=num.arange(0,100,1)
        yb=num.array(len(xb)*[0.0])+BermCrest # horizontal berm 100m long
    elif DuneCheck == 3 and RTR > 3: # user doesn't know, and not wave Dominated: no dunes, just berm
        xb=num.arange(0,100,1)
        yb=num.array(len(xb)*[0.0])+BermCrest # horizontal berm 100m long          
    else: # dune exists; we'll create it as sinusoid for representation
        xb=num.arange(0,1000.1,1)
        if BermLength <> 0: # Berm width in front of dune
            # berm profile
            yb=num.array(len(xb)*[0.0])+BermCrest
            Toe=abs(xb-BermLength).argmin()# locate toe to separate berm and dune
        else: Toe=0
        
        # dune profile
        DuneWidth=3*DuneCrest # width of sinusoid....won't use...for plotting purposes only
        yb[Toe:-1]=float(DuneCrest)*num.sin(2*pi*(xb[Toe:-1]-xb[Toe])/float(DuneWidth))+(BermCrest)
        DunePlotEnd=xb[Toe]+3*DuneWidth
        DunePlotSmall=xb[Toe]+DuneCrest

        out=num.arange(DunePlotEnd,len(yb),1)
        yb[DunePlotSmall:-1]=yb[DunePlotSmall:-1]/10
        yb[DunePlotSmall:-1]=yb[DunePlotSmall:-1]+yb[DunePlotSmall-1]-yb[DunePlotSmall]

        xb=num.delete(xb,out,None)
        yb=num.delete(yb,out,None); yb[-1]=yb[-2];
  
    # combine all vectors together
    xf=xf+xd[-1];xb=xb+xf[-1] # make one long x-axis
    xd=xd.tolist();yd=yd.tolist() # transform into lists
    xf=xf.tolist();yf=yf.tolist()
    xb=xb.tolist();yb=yb.tolist()
  
    yd.extend(yf);xd.extend(xf) # make one y-axis
    xd.extend(xb);yd.extend(yb)
    yd=num.array(yd);xd=num.array(xd);
    
elif BackHelp==2: # Modify profile   
    Xmod=[Dx[i] for i in range(lx)];Xmod=num.array(Xmod);
    Ymod=[Dmeas[i] for i in range(lx)];Ymod=num.array(Ymod);
    #Modify existing profile    
    if SlopeMod1<>0: #Modification 1
        if ShoreMod1<OffMod1:
            gp.AddError("In Modification 1, XInshore should be larger than XOffshore.")
            raise Exception
        Xmod,Ymod=SlopeModif(Xmod,Ymod,SlopeMod1,OffMod1,ShoreMod1)
    if SlopeMod2<>0: #Modification 2
        if ShoreMod2<OffMod2:
            gp.AddError("In Modification 2, XInshore should be larger than XOffshore.")
            raise Exception
        Xmod,Ymod=SlopeModif(Xmod,Ymod,SlopeMod2,OffMod2,ShoreMod2)
    if SlopeMod3<>0: #Modification 3
        if ShoreMod3<OffMod3:
            gp.AddError("In Modification 3, XInshore should be larger than XOffshore.")
            raise Exception
        Xmod,Ymod=SlopeModif(Xmod,Ymod,SlopeMod3,OffMod3,ShoreMod3)

    #Remove portions of existing profile
    if (OffDel1+ShoreDel1)<>0: #Removal 1
        Xmod,Ymod=DataRemove(Xmod,Ymod,OffDel1,ShoreDel1)
    if (OffDel2+ShoreDel2)<>0: #Removal 2
        Xmod,Ymod=DataRemove(Xmod,Ymod,OffDel2,ShoreDel2)

    #Smooth the signal
    xd=Xmod[:];
    yd=SignalSmooth.smooth(Ymod,int(SmoothParameter),'flat')
    
#__Plot
gp.AddMessage("\nPlotting Profile...")
#depth limits for plotting
AbvHT=num.nonzero(yd>HT+2);AbvHT=AbvHT[0]
AtMSLoc=num.nonzero(yd>0);AtMSLoc=AtMSLoc[0];
if len(AtMSLoc)>0:
    AtMSLoc=AtMSLoc[0]
    AtMSL=xd[AtMSLoc]
else: AtMSL=xd[1]


# plot and save
##subplot(221)
plot(Dx,Dmeas,'r',xd,yd);grid();hold
plot(xd,yd*0,'k',xd,yd*0-MSL,'--k',xd,yd*0+HT,'--k') 
ylabel('Elevation [m]', size='large')
##
##subplot(222)
##plot(xd,yd);grid()
##xlim(AtMSL-10,xd[-1]+10);ylim(-1,max(yd)+2)
##
##if BackHelp==1: # Create Backshore profile  
##    subplot (223)
##    plot(Dx,Dmeas,'r',xd,yd);grid()
####        legend(('Initial Profile','Modified Profile'),'lower right')
##
##elif BackHelp==2:
##    subplot (223)
##    plot(Xmod,Ymod,'r',xd,yd);grid()
####        legend(('Initial Profile','Modified Profile'),'lower right')
##
##    subplot (224)
####        legend(('Initial Profile','Modified Profile'),'lower right')
##else: #no backshore modif req'd
##    plot(Dx,Dmeas,'r',xd,yd);grid()


# save plot to .PNG
savefig(Profile_Plot, dpi=(640/8))

# create txt profile for created portion
##yd2=yd[::-1]# reverse depth profile
yd2=yd# reverse depth profile
file = open(CreatedProfile, "w")
for i in range(0,len(yd2)):
    file.writelines(str(xd[i])+"\t"+str(yd2[i])+"\n")
file.close()

# copy both profiles into scratch workspace
for filename in os.listdir(outputws):
    source_file = os.path.join(outputws, filename)
    if ((source_file.endswith("txt")) and ("Profile" in filename or "Created" in filename)):
        dest_file = os.path.join(scratchws, filename[:-4]+"_"+now.strftime("%Y-%m-%d-%H-%M")+".txt")
        shutil.copyfile(source_file, dest_file)


if WW3_Pts or FetchQuestion == 'Yes':
    # buffer 'LandPoint' by 100,000 meters
    gp.Buffer_analysis(LandPoint, LandPoint_Buff100k, "100000 Meters", "FULL", "ROUND", "NONE", "")
    
    # convert buffered 'LandPoint' into bathy polygon
    gp.Extent = LandPoint_Buff100k

    # grab projection spatial reference from 'LandPoly' input
    dataDesc = gp.describe(LandPoly)
    spatialRef = dataDesc.SpatialReference
    gp.CreateFeatureClass_management(interws, "Fetch_AOI.shp", "POLYGON", "#", "#", "#", spatialRef)

    # grab four corners from 'PtsCopyLR'
    CoordList = shlex.split(gp.Extent)

    # when creating a polygon, the coordinates for the starting point must be the same as the coordinates for the ending point
    cur = gp.InsertCursor(Fetch_AOI)
    row = cur.NewRow()
    PolygonArray = gp.CreateObject("Array")
    pnt = gp.CreateObject("Point")
    pnt.x = float(CoordList[0])
    pnt.y = float(CoordList[1])
    PolygonArray.add(pnt)
    pnt.x = float(CoordList[0])
    pnt.y = float(CoordList[3])
    PolygonArray.add(pnt)
    pnt.x = float(CoordList[2])
    pnt.y = float(CoordList[3])
    PolygonArray.add(pnt)
    pnt.x = float(CoordList[2])
    pnt.y = float(CoordList[1])
    PolygonArray.add(pnt)
    pnt.x = float(CoordList[0])
    pnt.y = float(CoordList[1])
    PolygonArray.add(pnt)
    row.shape = PolygonArray
    cur.InsertRow(row)
    del row, cur

    # erase from 'Fetch_AOI' areas where there is land
    LandPoly = AddField(LandPoly, "ERASE", "SHORT", "0", "0")
    gp.CalculateField_management(LandPoly, "ERASE", "1", "VB")
    UnionExpr = Fetch_AOI+" 1; "+LandPoly+" 2"        
    gp.Union_analysis(UnionExpr, UnionFC)

    # select features where "ERASE = 0"
    gp.Select_analysis(UnionFC, SeaPoly, "\"ERASE\" = 0")

if FetchQuestion == 'Yes':
    # create fetch vectors
    gp.AddMessage("\nComputing Fetch Vectors...")
    
    # copy original point twice and add fields to second copy
    gp.CopyFeatures_management(LandPoint, PtsCopy, "", "0", "0", "0")
    gp.CopyFeatures_management(LandPoint, PtsCopy2, "", "0", "0", "0")
    PtsCopy2 = AddField(PtsCopy2, "DISTANCE", "SHORT", "8", "")
    PtsCopy2 = AddField(PtsCopy2, "BEARING", "DOUBLE", "", "")
    PtsCopy2 = AddField(PtsCopy2, "BISECTANG", "DOUBLE", "", "")

    CopyExpr = PtsCopy
    for i in range(0,((BearingsNum*9)+BearingsNum)-2):
        CopyExpr = CopyExpr + ";"+PtsCopy

    BiSectAngFullList = []
    BiSectAngList = [0.0, 0.15707963267948966, 0.11780972450961724, 0.078539816339744828, 0.039269908169872414, \
                     0.0, 0.039269908169872414, 0.078539816339744828, 0.11780972450961724, 0.15707963267948966, 0.0]

    for i in range(0,16):
        for j in range(0,10):
            BiSectAngFullList.append(BiSectAngList[j])
        
    gp.Append_management(CopyExpr, PtsCopy2, "NO_TEST", "","")
    gp.CalculateField_management(PtsCopy2, "DISTANCE", str(RadLineDist), "PYTHON", "")

    # translate information from list into perp transect attribute table
    cur = gp.UpdateCursor(PtsCopy2, "", "", "BEARING; FID; BISECTANG")
    row = cur.Next()
    m = 0
    while row:
        FID = float(row.GetValue("FID"))
        Bearing = float((360.000/((BearingsNum*9)+BearingsNum))* FID)
        row.SetValue("Bearing", Bearing)
        row.SetValue("BiSectAng", BiSectAngFullList[m])
        m = m + 1
        cur.UpdateRow(row)
        row = cur.Next()
    del cur    
    del row

    # get the parameters
    fc = string.replace(PtsCopy2,"\\","/")
    # describe
    descfc = gp.describe(fc)
    sr = descfc.spatialreference
    # process the feature class attributes
    lstfc = string.split(fc,"/")
    for fl in lstfc:
        fn = fl
    strWorkspace = string.replace(fc,fl,"")
    gp.workspace = strWorkspace
    # shapefile
    newfn = string.replace(fl, ".shp", "_lineRotate.shp")
    # check for existence
    if gp.exists(strWorkspace + newfn):
        gp.delete_management(strWorkspace + newfn )
        gp.refreshcatalog(gp.workspace)
    # create the feature class
    gp.CreateFeatureClass_management(gp.workspace, newfn, "POLYLINE", fc, "SAME_AS_TEMPLATE", "SAME_AS_TEMPLATE", sr)
    addrecs = gp.insertcursor(strWorkspace + newfn)
    # refresh the catalog
    gp.refreshcatalog(gp.workspace)
      
    recs = gp.SearchCursor(fc)
    rec = recs.next()
    lstFields = gp.listfields(fc)
    while rec:
        # get the angle
        rotation = rec.getvalue("BEARING")
        length = rec.getvalue("DISTANCE")
        bearing = math.radians(rotation)
        angle = math.radians((360 - math.degrees(bearing)) + 90)        
        # get the feature and compute the to point
        pt = rec.shape.getpart(0)
        x = operator.add(math.cos(angle) * length, pt.x)
        y = operator.add(math.sin(angle) * length, pt.y)
        # build up the record
        addrec = addrecs.newrow()    
        # create the shape
        newArray = gp.createobject("array")
        newArray.add (pt)
        newPt = gp.createobject("point")
        newPt.x = x
        newPt.y = y
        newArray.add(newPt)
        # maintain the attributes
        lstFields.reset()
        fld = lstFields.next()
        while fld:
            if fld.name <> "FID" and fld.name <> "OBJECTID" and fld.name <> "SHAPE":
                addrec.setvalue(fld.name, rec.getvalue(fld.name))
            fld = lstFields.next()
        # add shape
        addrec.shape = newArray
        addrecs.insertrow(addrec)
        rec = recs.next()

    # erase parts of line where it overlaps land (works for ArcView)
    gp.Intersect_analysis(PtsCopyLR+" 1;"+SeaPoly+" 2", PtsCopyEL, "ALL", "", "INPUT")
    gp.MultipartToSinglepart_management(PtsCopyEL, PtsCopyExp)
    # convert to layer to select only lines originating from point source
    gp.MakeFeatureLayer_management(PtsCopyExp, PtsCopyExp_Lyr, "", gp.workspace, "")
    gp.SelectLayerByLocation_management(PtsCopyExp_Lyr, "WITHIN_A_DISTANCE", LandPoint, "20 Meters", "NEW_SELECTION")
    gp.CopyFeatures_management(PtsCopyExp_Lyr, Fetch_Vectors, "", "0", "0", "0")
    # add and calculate "LENGTH_M" field
    Fetch_Vectors = AddField(Fetch_Vectors, "LENGTH_M", "LONG", "6", "")
    gp.CalculateField_management(Fetch_Vectors, "LENGTH_M", "!shape.length@meters!", "PYTHON", "")

    # populate fetch distances to a list
    AngleList = [0.0, 22.5, 45.0, 67.5, 90.0, 112.5, 135.0, 157.5, 180.0, 202.5, 225.0, 247.5, 270.0, 292.5, 315.0, 337.5]
    FetchList = [0.0]*16
    # translate information from list into perp transect attribute table
    cur = gp.UpdateCursor(Fetch_Vectors, "", "", "BEARING; LENGTH_M")
    row = cur.Next()
    while row:
        Angle = float(row.GetValue("BEARING"))
        if Angle in AngleList:
            indexAngle = AngleList.index(Angle)
            FetchList[indexAngle] = float(row.GetValue("LENGTH_M"))
        row = cur.Next()
    del cur    
    del row

    binD1 = []; binBiAng1 = []
    binD2 = []; binBiAng2 = []
    binD3 = []; binBiAng3 = []
    binD4 = []; binBiAng4 = []
    binD5 = []; binBiAng5 = []
    binD6 = []; binBiAng6 = []
    binD7 = []; binBiAng7 = []
    binD8 = []; binBiAng8 = []
    binD9 = []; binBiAng9 = []
    binD10 = []; binBiAng10 = []
    binD11 = []; binBiAng11 = []
    binD12 = []; binBiAng12 = []
    binD13 = []; binBiAng13 = []
    binD14 = []; binBiAng14 = []
    binD15 = []; binBiAng15 = []
    binD16 = []; binBiAng16 = []

    cur = gp.UpdateCursor(Fetch_Vectors, "", "", "BEARING; LENGTH_M; BISECTANG")
    row = cur.Next()    
    while row:
        Bearing = float(row.GetValue("BEARING"))
        if Bearing >= 2.25 and Bearing <= 20.25:
            binD1.append(row.GetValue("LENGTH_M"))
            binBiAng1.append(row.GetValue("BiSectAng"))
        elif Bearing >= 24.75 and Bearing <= 42.75:
            binD2.append(row.GetValue("LENGTH_M"))
            binBiAng2.append(row.GetValue("BiSectAng"))
        elif Bearing >= 47.25 and Bearing <= 65.25:
            binD3.append(row.GetValue("LENGTH_M"))
            binBiAng3.append(row.GetValue("BiSectAng"))
        elif Bearing >= 69.75 and Bearing <= 87.75:
            binD4.append(row.GetValue("LENGTH_M"))
            binBiAng4.append(row.GetValue("BiSectAng"))
        elif Bearing >= 92.25 and Bearing <= 110.25:
            binD5.append(row.GetValue("LENGTH_M"))
            binBiAng5.append(row.GetValue("BiSectAng"))
        elif Bearing >= 114.75 and Bearing <= 132.75:
            binD6.append(row.GetValue("LENGTH_M"))
            binBiAng6.append(row.GetValue("BiSectAng"))
        elif Bearing >= 137.25 and Bearing <= 155.25:
            binD7.append(row.GetValue("LENGTH_M"))
            binBiAng7.append(row.GetValue("BiSectAng"))
        elif Bearing >= 159.75 and Bearing <= 177.75:
            binD8.append(row.GetValue("LENGTH_M"))
            binBiAng8.append(row.GetValue("BiSectAng"))
        elif Bearing >= 182.25 and Bearing <= 200.25:
            binD9.append(row.GetValue("LENGTH_M"))
            binBiAng9.append(row.GetValue("BiSectAng"))
        elif Bearing >= 204.75 and Bearing <= 222.75:
            binD10.append(row.GetValue("LENGTH_M"))
            binBiAng10.append(row.GetValue("BiSectAng"))
        elif Bearing >= 227.25 and Bearing <= 245.25:
            binD11.append(row.GetValue("LENGTH_M"))
            binBiAng11.append(row.GetValue("BiSectAng"))
        elif Bearing >= 249.75 and Bearing <= 267.75:
            binD12.append(row.GetValue("LENGTH_M"))
            binBiAng12.append(row.GetValue("BiSectAng"))
        elif Bearing >= 272.25 and Bearing <= 290.25:
            binD13.append(row.GetValue("LENGTH_M"))
            binBiAng13.append(row.GetValue("BiSectAng"))
        elif Bearing >= 294.75 and Bearing <= 312.75:
            binD14.append(row.GetValue("LENGTH_M"))
            binBiAng14.append(row.GetValue("BiSectAng"))
        elif Bearing >= 317.25 and Bearing <= 335.25:
            binD15.append(row.GetValue("LENGTH_M"))
            binBiAng15.append(row.GetValue("BiSectAng"))
        elif Bearing >= 339.75 and Bearing <= 357.75:
            binD16.append(row.GetValue("LENGTH_M"))
            binBiAng16.append(row.GetValue("BiSectAng"))
        cur.UpdateRow(row)
        row = cur.Next()
    del row, cur
    
    # use 'FetchMean' function to summarize bins
    def FetchCalc(binD, binBiAng, index):
        if len(binD) > 0:
            numer = 0.0
            denom = 0.0
            for i in range(0,len(binD)):
                numer = numer + binD[i]*num.cos(binBiAng[i])
                denom = denom + num.cos(binBiAng[i])
            FetchList[index] = (numer/denom)
        return FetchList

    FetchList = num.zeros(16, dtype=num.float64)
    FetchCalc(binD4, binBiAng4, 0); FetchCalc(binD3, binBiAng3, 1); FetchCalc(binD2, binBiAng2, 2); FetchCalc(binD1, binBiAng1, 3)
    FetchCalc(binD16, binBiAng16, 4); FetchCalc(binD15, binBiAng15, 5); FetchCalc(binD14, binBiAng14, 6); FetchCalc(binD13, binBiAng13, 7)
    FetchCalc(binD12, binBiAng12, 8); FetchCalc(binD11, binBiAng11, 9); FetchCalc(binD10, binBiAng10, 10); FetchCalc(binD9, binBiAng9, 11)
    FetchCalc(binD8, binBiAng8, 12); FetchCalc(binD7, binBiAng7, 13); FetchCalc(binD6, binBiAng6, 14); FetchCalc(binD5, binBiAng5, 15)

    gp.AddMessage("Fetch Distances (from 90 degrees, counter clockwise): \n"+str(FetchList))

    # plot fetch on rose    
    radians = (num.pi / 180.0)
    pi = num.pi
    theta16 = [0*radians,22.5*radians,45*radians,67.5*radians,90*radians,112.5*radians,135*radians,157.5*radians,180*radians,202.5*radians,225*radians,247.5*radians,270*radians,292.5*radians,315*radians,337.5*radians]
    rc('grid', color='#316931', linewidth=1, linestyle='-')
    rc('xtick', labelsize=0)
    rc('ytick', labelsize=15)
    # force square figure and square axes looks better for polar, IMO
    width, height = matplotlib.rcParams['figure.figsize']
    size = min(width, height)
    # make a square figure
    plt = figure(figsize=(size, size))
    ax = plt.add_axes([0.1, 0.1, 0.8, 0.8], polar=True, axisbg='w')
    # plot
    bars = ax.bar(theta16, FetchList, width=.35, color='#ee8d18', lw=1)
    for r,bar in zip(FetchList, bars):
        bar.set_facecolor(cm.YlOrRd(r/10.))
        bar.set_alpha(.65)
    ax.set_rmax(max(FetchList)+1)
    grid(True)
    ax.set_title("Average Fetch (meters)", fontsize=15)
    plt.savefig(Fetch_Plot, dpi=(640/8))

#_____Read WW3 Info
if WW3_Pts:
    gp.AddMessage("\nReading Wave Watch III Information...")

    # create cost surface based on 'SeaPoly'
    gp.Extent = Fetch_AOI
    projection = grabProjection(LandPoint)
    gp.Project_management(WW3_Pts, WW3_Pts_prj, projection)
    SeaPoly = AddField(SeaPoly, "SEA", "SHORT", "", "")
    gp.CalculateField_management(SeaPoly, "SEA", "1", "PYTHON", "")
    gp.FeatureToRaster_conversion(SeaPoly, "SEA", seapoly_rst, "250")
    gp.Expand_sa(seapoly_rst, seapoly_e, "1", "1")
    # allocate 'WW3_Pts' throughout cost surface
    gp.CostAllocation_sa(WW3_Pts_prj, seapoly_e, costa_ww3, "", "", "FID", "", "")
    # determine which point is closest to 'LandPoint'
    gp.ExtractValuesToPoints_sa(LandPoint, costa_ww3, LandPoint_WW3, "NONE")
    cur = gp.UpdateCursor(LandPoint_WW3)
    row = cur.Next()
    WW3_FID = row.GetValue("RASTERVALU")
    del row
    del cur

    # populate list with data from closest WW3 point
    WW3_ValuesList = []
    dirList = [0, 22, 45, 67, 90, 112, 135, 157, 180, 202, 225, 247, 270, 292, 315, 337]
    letterList = ['e','f','g','h','i','j','k','l','m','n','o','p','q','r','s','t']
    SrchCondition = "FID = "+str(WW3_FID)
    cur = gp.SearchCursor(WW3_Pts_prj, SrchCondition, "", "")
    row = cur.Next()
    WW3_ValuesList.append(row.GetValue("LAT")) # 0
    WW3_ValuesList.append(row.GetValue("LONG")) # 1
    for i in range(0,len(dirList)):
        WW3_ValuesList.append(row.GetValue("V10PCT_"+str(dirList[i]))) # 2 - 17
    for i in range(0,len(dirList)):
        WW3_ValuesList.append(row.GetValue("V25PCT_"+str(dirList[i]))) # 18 - 33
    for i in range(0,len(dirList)):
        WW3_ValuesList.append(row.GetValue("V_MAX_"+str(dirList[i]))) # 34 - 49
    WW3_ValuesList.append(row.GetValue("V_10YR")) # 50
    WW3_ValuesList.append(row.GetValue("H_10PCT")) # 51
    WW3_ValuesList.append(row.GetValue("T_10PCT")) # 52
    WW3_ValuesList.append(row.GetValue("H_25PCT")) # 53
    WW3_ValuesList.append(row.GetValue("T_25PCT")) # 54
    WW3_ValuesList.append(row.GetValue("H_MAX")) # 55
    WW3_ValuesList.append(row.GetValue("T_MAX")) # 56
    WW3_ValuesList.append(row.GetValue("H_10YR")) # 57
    WW3_ValuesList.append(row.GetValue("He")) # 58
    WW3_ValuesList.append(row.GetValue("Hmod")) # 59
    WW3_ValuesList.append(row.GetValue("Tmod")) # 60
    del row
    del cur

    # import Profile Builder info from Excel file
    xlApp = Dispatch("Excel.Application")
    xlApp.Visible=0
    xlApp.DisplayAlerts=0
    xlApp.Workbooks.Open(InputTable)
    
    # write WW3 results to Excel sheet 'Erosion Model Input'
    cell2 = xlApp.Worksheets("ErosionModelInput")
    # maximum wave height
    cell2.Range("e85").Value = WW3_ValuesList[55]
    cell2.Range("f85").Value = WW3_ValuesList[56]
    # top 10% wave height
    cell2.Range("e86").Value = WW3_ValuesList[51]
    cell2.Range("f86").Value = WW3_ValuesList[52]
    # top 25% wave height
    cell2.Range("e87").Value = WW3_ValuesList[53]
    cell2.Range("f87").Value = WW3_ValuesList[54]
    # 10-yr wave height
    cell2.Range("e88").Value = WW3_ValuesList[57]
    # maximum wind speed
    for i in range(34,50):
        cell2.Range(letterList[i-34]+"89").Value = WW3_ValuesList[i]
    # top 10% wind speed
    for i in range(2,18):
        cell2.Range(letterList[i-2]+"90").Value = WW3_ValuesList[i]
    # top 25% wind speed
    for i in range(18,34):
        cell2.Range(letterList[i-18]+"91").Value = WW3_ValuesList[i]
    # 10-yr wind speed
    cell2.Range("e92").Value = WW3_ValuesList[50]
    
    xlApp.ActiveWorkbook.Close(SaveChanges=1) # save changes
    xlApp.Quit()

##    # Define He,Hmod and Tmod in case user doesn't enter these value
##    He=WW3_ValuesList[58];
##    Hm=WW3_ValuesList[59]; Tm=WW3_ValuesList[60];

gp.AddMessage("\nCreating outputs...")
# return projected point to geographic (unprojected)
gp.Project_management(LandPoint, LandPoint_Geo, geo_projection)
# grab coordinates for Google Maps plot
cur = gp.UpdateCursor(LandPoint_Geo)
row = cur.Next()
feat = row.Shape
midpoint1 = feat.Centroid
midList1 = shlex.split(midpoint1)
midList1 = [float(s) for s in midList1]
del cur
del row
PtLat = str(midList1[1])
PtLong = str(midList1[0])

# create html file
htmlfile = open(ProfileErosion_HTML, "w")
htmlfile.write("<html>\n")
htmlfile.write("<title>Marine InVEST</title>")
htmlfile.write("<CENTER><H1>Visualizing Coastal Protection - Tier 1</H1></CENTER>")
htmlfile.write("<br><HR><H2>Map and Plots</H2>\n")
htmlfile.write("This map and plots showing the location and characteristics of xxx from the Profile Generator and Erosion model runs. <br>\n")
htmlfile.write("<table border=\"0\"><tr><td>")
htmlfile.write("<iframe width=\"640\" height=\"640\" frameborder=\"0\" scrolling=\"no\" marginheight=\"0\" marginwidth=\"0\"") 
htmlfile.write("src=\"http://maps.google.com/maps/api/staticmap?center=")
htmlfile.write(PtLat+","+PtLong)
htmlfile.write("&zoom=11&size=640x640&maptype=hybrid&markers=color:red%7Ccolor:red%7Clabel:X%7C")
htmlfile.write(PtLat+","+PtLong)
htmlfile.write("&sensor=false\"></iframe><br/><small><a href=\"http://maps.google.com/maps?f=q&amp;source=embed&amp;hl=en&amp;geocode=&amp;q=")
htmlfile.write(PtLat+","+PtLong)
htmlfile.write("&amp;aq=&amp;")
htmlfile.write("sll=37.160317,-95.712891&amp;sspn=48.113934,71.455078&amp;ie=UTF8&amp;z=10&amp;ll=")
htmlfile.write(PtLat+","+PtLong)
htmlfile.write("\" style=\"color:#0000FF;text-align:center\">View Larger Map</a></small><br>\n")
htmlfile.write("</td><td>")
htmlfile.write("<img src=\"Fetch_Plot.png\" alt=\"Fetch Distance Plot\">")
htmlfile.write("</td></tr><tr><td>")
htmlfile.write("<img src=\"Profile_Plot.png\" alt=\"Profile Generator Plot\" width=\"640\" height=\"480\">")
htmlfile.write("</td><td>")
htmlfile.write("<img src=\"Erosion_Plot.png\" alt=\"Erosion Plot\" width=\"640\" height=\"480\">")
htmlfile.write("</table><br>\n")
htmlfile.write("<br><HR><H2>Site Information</H2>\n")
htmlfile.write("<li><u>The site is located at</u> - Latitude: "+PtLat+", Longitude: "+PtLong+"<br>\n")
htmlfile.write("<li><u>The tidal range is</u>: xxx m (High Tide value)<br>\n")
htmlfile.write("<li><u>The foreshore slope is</u>: xxx<br>\n")
htmlfile.write("<li><u>The backshore has a slope that is</u>: xxx m high and yyy m long<br>\n")
htmlfile.write("<li><u>The beach is backed by a sand dune that is</u>: xxx m high<br>\n")
htmlfile.write("<li>There is vegetation in the sub- and inter-tidal area.  The vegetation characteristics are xxx.<br>\n")
htmlfile.close()


# create parameter file
parameters.append("Script location: "+os.path.dirname(sys.argv[0])+"\\"+os.path.basename(sys.argv[0]))
parafile = open(outputws+"parameters_"+now.strftime("%Y-%m-%d-%H-%M")+".txt","w") 
parafile.writelines("PROFILE GENERATOR PARAMETERS\n")
parafile.writelines("____________________________\n\n")
     
for para in parameters:
    parafile.writelines(para+"\n")
    parafile.writelines("\n")
parafile.close()