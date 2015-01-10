import logging
import pysam
import vcf
from sv_interval import SVInterval
import sys
import argparse
import os

logger = logging.getLogger(__name__)

mydir = os.path.dirname(os.path.realpath(__file__))

'''
from http://gmt.genome.wustl.edu/packages/pindel/user-manual.html

There is a head line for each variant reported, followed by the alignment of supporting reads to the reference on the
second line. The example variants are a 1671bp deletion and a 10bp insertion on chr20. The breakpoints are specified
after "BP". Due to microhomology around the breakpoints, the breakpoint coordinates may shift both upstream and
downstream,'BP_range' is used to indicate the range of this shift. The header line contains the following data:

1) The index of the indel/SV (57 means that 57 insertions precede this insertion in the file)

2) The type of indel/SV: I for insertion, D for deletion, INV for inversion, TD for tandem duplication

3) The length of the SV

4) "NT" (to indicate that the next number is the length of non-template sequences inserted; insertions are fully covered
 by the NT-fields, deletions can have NT bases if the deletion is not 'pure', meaning that while bases have been
 deleted, some bases have been inserted between the breakpoints)

5) the length(s) of the NT fragment(s)

6) the sequence(s) of the NT fragment(s)

7-8) the identifier of the chromosome the read was found on

9-10-11) BP: the start and end positions of the SV

12-13-14) BP_range: if the exact position of the SV is unclear since bases at the edge of one read-half could equally
well be appended to the other read-half. In the deletion example, ACA could be on any side of the gap, so the original
deletion could have been between 1337143 and 1338815, between 1337144 and 1338816, or between 1337145 and 133817, or
between 1337146 and 133818. BP-range is used to indicate this range.

15) "Supports": announces that the total count of reads supporting the SV follow.

16) The number of reads supporting the SV

17) The number of unique reads supporting the SV (so not counting duplicate reads)

18) +: supports from reads whose anchors are upstream of the SV

19-20) total number of supporting reads and unique number of supporting reads whose anchors are upstream of the SV.

21) -: supports from reads whose anchors are downstream of the SV

22-23) total number of supporting reads and unique number of supporting reads whose anchors are downstream of the SV

24-25) S1: a simple score, ("# +" + 1)* ("# -" + 1) ;

26-27) SUM_MS: sum of mapping qualities of anchor reads, The reads with variants or unmapped are called split-read,
whose mate is called anchor reads. We use anchor reads to narrow down the search space to speed up and increase
sensitivity;

28) the number of different samples scanned

29-30-31) NumSupSamples?: the number of samples supporting the SV, as well as the number of samples having unique
reads supporting the SV (in practice, these numbers are the same)

32+) Per sample: the sample name, followed by the total number of supporting reads whose anchors are upstream, the
total number of unique supporting reads whose anchors are upstream, the total number of supporting reads whose anchors
 are downstream, and finally the total number of unique supporting reads whose anchors are downstream.

----

The reported larger insertion (LI) record is rather different than other types of variants. Here is the format:
1) index,
2) type(LI),
3) "ChrID",
4) chrName,
5) left breakpoint,
6) "+"
7) number of supporting reads for the left coordinate,
8) right breakpoint,
9) "-"
10) number of supporting reads for the right coordinate.

Following lines are repeated for each sample
11) Sample name
12) "+"
13) upstream supporting reads
14) "-"
15) downstream supporting reads

'''


pindel_source = set(["Pindel"])
min_coverage = 10
het_cutoff = 0.2
hom_cutoff = 0.8

GT_REF = "0/0"
GT_HET = "0/1"
GT_HOM = "1/1"

PINDEL_TO_SV_TYPE = {"I": "INS", "D": "DEL", "LI": "INS", "TD": "DUP:TANDEM", "INV": "INV"}


class PindelRecord:
  def __init__(self, record_string, reference_handle):
    fields = record_string.split()
    self.sv_type = fields[1]

    if self.sv_type != "LI":
      self.sv_len = int(fields[2])
      self.num_nt_added = map(int, fields[4].split(":"))
      self.nt_added = map(lambda x: x.replace('"', ''), fields[5].split(":"))
      self.chromosome = fields[7]
      self.start_pos = int(fields[9])
      self.end_pos = int(fields[10])
      self.bp_range = (int(fields[12]), int(fields[13]))
      self.read_supp = int(fields[15]) # The number of reads supporting the SV
      self.uniq_read_supp = int(fields[16]) # The number of unique reads supporting SV (not count duplicate reads)
      self.up_read_supp = int(fields[18]) # upstream
      self.up_uniq_read_supp = int(fields[19])
      self.down_read_supp = int(fields[21]) # downstream
      self.down_uniq_read_supp = int(fields[22])
      self.simple_score = int(fields[24])
      self.sum_mapq = int(fields[26]) # sum of mapping qualities of anchor reads
      self.num_sample = int(fields[27]) # number of samples
      self.num_sample_supp = int(fields[29]) # number of samples with supporting reads
      self.num_sample_uniq_supp = int(fields[30]) # number of sample with unique supporting readas
      self.homlen = self.bp_range[1] - self.end_pos
      self.homseq = reference_handle.fetch(self.chromosome, self.end_pos-1, self.bp_range[1]-1)
      self.samples = [{"name": fields[i], "ref_support_at_start": int(fields[i+1]), "ref_support_at_end": int(fields[i+2]), "plus_support": int(fields[i+3]), "minus_support": int(fields[i+4])} for i in xrange(31, len(fields), 5)]
    else:
      self.sv_len = 0
      self.chromosome = fields[3]
      self.start_pos = int(fields[4])
      self.up_read_supp = int(fields[6]) # upstream
      self.end_pos = int(fields[7])
      self.down_read_supp = int(fields[8]) # downstream
      self.bp_range = (self.start_pos, self.end_pos)
      self.homlen = 0
      self.homseq = ""
      self.samples = [{"name": fields[i], "plus_support": int(fields[i+2]), "minus_support": int(fields[i+4])} for i in xrange(10, len(fields), 5)]

    self.derive_genotype()

  def derive_genotype(self):
    if self.sv_type == "LI" or self.sv_type == "I":
      self.gt = GT_HET if (self.up_read_supp + self.down_read_supp) > 0 else GT_REF
      return

    total_event_reads = self.uniq_read_supp
    total_ref_reads = self.up_uniq_read_supp + self.down_uniq_read_supp
    if total_event_reads + total_ref_reads < min_coverage:
      self.gt = GT_REF
      return

    allele_fraction = float(total_event_reads) / (float(total_event_reads) + float(total_ref_reads))
    if allele_fraction < het_cutoff:
      self.gt = GT_REF
    elif allele_fraction < hom_cutoff:
      self.gt = GT_HET
    else:
      self.gt = GT_HOM

  def to_sv_interval(self):
    sv_type = PINDEL_TO_SV_TYPE[self.sv_type]
    if sv_type != "INS":
      return SVInterval(self.chromosome, self.start_pos, self.end_pos, "Pindel", sv_type=sv_type, length=self.sv_len, sources=pindel_source, native_sv=self)
    else:
      return SVInterval(self.chromosome, self.start_pos, self.start_pos, "Pindel", sv_type=sv_type, length=self.sv_len, sources=pindel_source, native_sv=self, wiggle=100, gt=self.gt)

  def to_vcf_record(self, sample):
    alt = ["<%s>" % (self.sv_type)]
    info = {"SVLEN": self.sv_len,
            "SVTYPE": self.sv_type,
            "PD_NUM_NT_ADDED": self.num_nt_added,
            "PD_NT_ADDED": self.nt_added,
            "PD_BP_RANGE_START": self.bp_range[0],
            "PD_BP_RANGE_END": self.bp_range[1],
            "PD_READ_SUPP": self.read_supp,
            "PD_UNIQ_READ_SUPP": self.uniq_read_supp,
            "PD_UP_READ_SUPP": self.up_read_supp,
            "PD_UP_UNIQ_READ_SUPP": self.up_uniq_read_supp,
            "PD_DOWN_READ_SUPP": self.down_read_supp,
            "PD_DOWN_UNIQ_READ_SUPP": self.down_uniq_read_supp,
            "PD_SIMPLE_SCORE": self.simple_score,
            "PD_SUM_MAPQ": self.sum_mapq,
            "PD_NUM_SAMPLE": self.num_sample,
            "PD_NUM_SAMPLE_SUPP": self.num_sample_supp,
            "PD_NUM_SAMPLE_UNIQ_SUPP": self.num_sample_uniq_supp,
            "PD_HOMLEN": self.homlen,
            "PD_HOMSEQ": self.homseq
            }

    if self.sv_type == "DEL" or self.sv_type == "INV" or self.sv_type == "DUP:TANDEM":
      info["END"] = self.pos1 + self.sv_len
    elif self.sv_type == "INS":
      info["END"] = self.pos1
    else:
      return None

    vcf_record = vcf.model._Record(self.chr1, self.pos1, ".", "N", alt, ".", ".", info, "GT", [vcf.model._Call(None, sample, [self.derive_genotype()])])
    return vcf_record

  def __str__(self):
    return str(self.__dict__)


class PindelReader:
  def __init__(self, file_name, reference_handle):
    logger.info("File is " + file_name)
    self.file_fd = open(file_name) if file_name is not None else sys.stdin
    self.reference_handle = reference_handle

  def __iter__(self):
    return self

  def next(self):
    while True:
      line = self.file_fd.next()
      if line.find("ChrID") >= 1:
        return PindelRecord(line.strip(), self.reference_handle)

def convert_pindel_to_vcf(file_name, sample, out_vcf):
  vcf_template_reader = vcf.Reader(open(os.path.join(mydir, "resources/template.vcf"), "r"))
  vcf_template_reader.samples = [sample]

  vcf_writer = vcf.Writer(open(out_vcf, "w"), vcf_template_reader)

  for pd_record in PindelReader(file_name):
    vcf_record = pd_record.to_vcf_record(sample)
    if vcf_record is None:
        continue
    vcf_writer.write_record(vcf_record)
  vcf_writer.close()


if __name__ == "__main__":
  parser = argparse.ArgumentParser("Convert Pindel output file to VCF")
  parser.add_argument("--pindel_in", help = "Pindel output file", required = False)
  parser.add_argument("--vcf_out", help = "Output VCF to create", required = False)
  parser.add_argument("--sample", help = "Sample name", required = True)

  args = parser.parse_args()
  convert_pindel_to_vcf(args.pindel_in, args.sample, args.vcf_out)
