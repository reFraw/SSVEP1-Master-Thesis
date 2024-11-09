import socket
import numpy as np
import scipy as sp
import matplotlib.pyplot as plt

class SsvepDetector():
    
    def __init__(self):
        self.notchFreq = 50
        
    def __applyNotchFilter(self, signal, samplingRate):
        
        b, a = sp.signal.iirnotch(self.notchFreq, 30, samplingRate)
        notched = sp.signal.lfilter(b, a, signal)
        
        return notched
        
    def __applyBandpassFilter(self,signal, samplingRate, lowcut, highcut, order=4):
        
        nyq = 0.5 * samplingRate
        low = lowcut / nyq
        high = highcut / nyq

        b, a = sp.signal.butter(order, [low, high], btype="band")
        
        filtered = sp.signal.lfilter(b, a, signal)
        
        return filtered
    
    def __getPSD(self, signal, samplingRate):
        
        nSample = len(signal)
        dt = 1/samplingRate
        
        fftSignal = np.fft.fft(signal)
        
        fftFreq = np.fft.fftfreq(nSample, d=dt)
        fftFreq = fftFreq[:nSample // 2]
        
        psd = (np.abs(fftSignal) ** 2) / (nSample * samplingRate)
        psd = psd[:nSample // 2]
        
        return fftFreq, psd
    
    def predict(self, OzSignal, FzSignal, samplingRate, targets):
        """
        Determina la presenza di picchi SSVEP.
        
        Parameters:
        - OzSignal: array numpy.
        - FzSignal: array numpy.
        - SamplingRate: Frequenza di campionamento del segnale.
        - targets: lista con le frequenze di interesse.
        """
        
        freqTolerance = 0.5
        
        FzNotched = self.__applyNotchFilter(FzSignal, samplingRate)
        FzFiltered = self.__applyBandpassFilter(FzNotched, samplingRate, 10, 30, 5)
        
        OzNotched = self.__applyNotchFilter(OzSignal, samplingRate)
        OzFiltered = self.__applyBandpassFilter(OzNotched, samplingRate, 10, 30, 5)
        
        differenceSig = OzFiltered - FzFiltered
        
        f, diffPSD = self.__getPSD(differenceSig, samplingRate)
        
        peakIdx = np.argsort(diffPSD)[-2:]
        
        firstPeakFreq = f[peakIdx[-1]]
        secondPeakFreq = f[peakIdx[-2]]
        
        for target in targets:
            delta = np.abs(firstPeakFreq - target)
            if delta < 1:
                power = diffPSD[peakIdx[-1]]
                if power > 4.5:
                    return (target, firstPeakFreq)
                else:
                    return None
            else:
                if secondPeakFreq <= target + freqTolerance and secondPeakFreq >= target - freqTolerance:
                    power = diffPSD[peakIdx[-2]]
                    if power > max(1.5, 6 - 0.2 * target):
                        return (target, secondPeakFreq)
                    else:
                        return None

class MyOVBox(OVBox):
   
   def __init__(self):
      
        OVBox.__init__(self)
        
        self.signalHeader = None
        self.detector = None
        
        self.SamplingRate = None
        self.frequencies = None
        self.windowTime = None
        self.nTargets = None
        self.socket = None

   def initialize(self):
      
      self.SamplingRate = int(self.setting['Sampling rate'])
      self.windowTime = int(self.setting['Window time'])
      
      self.nTargets = int(self.setting['Number of targets'])
      
      self.targetFrequencies = np.zeros((self.nTargets,))
      for i in range(self.nTargets):
         self.targetFrequencies[i] = float(self.setting['Frequencies'].split(";")[i])
         
      self.detector = SsvepDetector()
      
      self.port = 5680
      self.ip = "localhost"
      
      self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      self.socket.connect((self.ip, self.port))
      
      # TODO : Collegare il socket.
      
   def uninitialize(self):
       
       self.socket.close()

   
   def process(self):

      for chunkIndex in range( len(self.input[0]) ):

         if(type(self.input[0][chunkIndex]) == OVSignalHeader):
            self.signalHeader = self.input[0].pop()
            outputHeader = OVSignalHeader(
            self.signalHeader.startTime,
            self.signalHeader.endTime,
            [1, self.signalHeader.dimensionSizes[1]],
            ['Oz']+self.signalHeader.dimensionSizes[1]*[''],
            self.signalHeader.samplingRate)

         elif(type(self.input[0][chunkIndex]) == OVSignalBuffer):
             
            chunk = self.input[0].pop()
            eegSignal = np.array(chunk).reshape(2, self.windowTime*self.SamplingRate)
            
            Oz = eegSignal[0, :]
            Fz = eegSignal[1, :]
            
            pred = self.detector.predict(Oz, Fz, self.SamplingRate, self.targetFrequencies)
            
            if pred != None:
               targetFreq = pred[0]
               peakFreq = pred[1]
               print(f"\nSSVEP detected at {targetFreq} Hz ({peakFreq} Hz). Sending message...")
               self.socket.sendall(str(targetFreq).split(".")[0].encode("utf-8"))
               
            else:
                print(f"\nRESTING")
         
box = MyOVBox()