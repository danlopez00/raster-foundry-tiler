package com.azavea.rasterfoundry

import com.azavea.rasterfoundry.io._

import org.apache.spark._
import org.apache.spark.rdd._
import geotrellis.raster._
import geotrellis.spark._
import com.typesafe.scalalogging.slf4j._
import spray.json._

object Main extends LazyLogging {
  def getSparkContext(): SparkContext = {
    val conf =
      new SparkConf()
        .setIfMissing("spark.master", "local[8]")
        .setIfMissing("spark.app.name", "Raster Foundry Tiler")
        .set("spark.serializer", "org.apache.spark.serializer.KryoSerializer")
        .set("spark.kryo.registrator", classOf[KryoRegistrator].getName)

    new SparkContext(conf)
  }

  def main(args: Array[String]): Unit = {
    val config = ConfigParser.parse(args, Config()).getOrElse(sys.exit())
    val status = new Status(config.statusQueue)        
    logger.info(s"Status Queue: ${config.statusQueue}")

    val jobRequest = {
      val uri = config.chunkerResult
      new java.net.URI(uri).getScheme match {
        case null =>
        scala.io.Source.fromFile(uri).getLines.mkString.parseJson.convertTo[JobRequest]
        case _ =>
        S3Client.default.readTextFile(uri).parseJson.convertTo[JobRequest]
      }
    }
    logger.info(s"Target: {jobRequest.target}")

    status.notifyStart(jobRequest.id)

    implicit val sc = getSparkContext()

    try {
      val inputImages: Seq[InputImageRDD] =
      jobRequest.inputImages

      val createSink: () => Sink = {
        val parsedTarget = new java.net.URI(jobRequest.target)
        parsedTarget.getScheme match {
          case null =>
            { () => new LocalSink(jobRequest.target) }
          case "s3" =>
            val bucket = parsedTarget.getHost
            val path = parsedTarget.getPath
            val key = path.substring(1, path.length)

            { () => new S3Sink(bucket, key) }
          case x =>
            throw new NotImplementedError(s"No sink implemented for scheme $x")
        }
      }

      Tiler(inputImages)(createSink)
    } catch {
      case e: Exception =>
        status.notifyFailure(jobRequest.id, e)
        throw e
    } finally {
      sc.stop
    }

    status.notifySuccess(jobRequest.id, jobRequest.target, jobRequest.inputImageDefinitions.map(_.sourceUri))
  }
}
